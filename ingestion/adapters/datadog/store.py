"""SQLite-backed staging store for Datadog API responses.

Decouples Datadog fetches from parsing so we can:
  * Replay parsing without re-hitting the API (and re-burning quota).
  * Buffer entire traces by `trace_id` for tree reconstruction.
  * Run multiple parsers (span buckets, trace patterns, drift) over the
    same staged data.
  * Survive parser crashes without losing fetched data.

Phase 1 ships the skeleton — connection management, migration runner, and
the `fetch_log` audit table. Per-API tables (spans, catalog, monitors,
SLOs) land in later phases as numbered SQL migrations under
`migrations/`.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Iterator, Literal

logger = logging.getLogger(__name__)

FetchStatus = Literal["success", "partial", "failed"]

_MIGRATION_FILE_RE = re.compile(r"^(\d{4})_([a-zA-Z0-9_]+)\.sql$")


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    sql: str


@dataclass(frozen=True)
class FetchRecord:
    """Latest-fetch summary for one API. Returned by `last_fetch`."""

    api: str
    fetched_at: datetime
    rows_written: int
    status: FetchStatus
    duration_ms: float
    error: str


class DatadogStore:
    """SQLite store for staged Datadog API responses.

    Usage:
        with DatadogStore("./out/datadog.db") as store:
            store.record_fetch(api="spans", rows_written=1247)
            if store.is_stale("catalog", ttl_seconds=3600):
                ...

    Pass `":memory:"` (the default) for tests. The store is single-threaded
    and not safe to share across processes; create one per pipeline run.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        # Friendlier behaviour: row dicts, foreign-key enforcement, write-ahead
        # logging for crash-resilience on disk-backed stores.
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._run_migrations()

    # ---- lifecycle -----------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DatadogStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def path(self) -> str:
        return self._path

    # ---- migrations ----------------------------------------------------

    def _run_migrations(self) -> None:
        """Apply any migration files not yet recorded in `_migrations`.

        Idempotent: each migration's CREATE statements use `IF NOT EXISTS`
        so partially-applied state (DDL committed but tracking row missing,
        e.g. crash mid-migration) self-heals on the next run.
        """
        migrations = _discover_migrations()
        if not migrations:
            return

        # First migration creates `_migrations` itself; bootstrap the table
        # by running version 1 unconditionally if it isn't there yet.
        if not self._migrations_table_exists():
            self._apply_migration(migrations[0])
            migrations = migrations[1:]

        applied = self._applied_versions()
        for migration in migrations:
            if migration.version in applied:
                continue
            self._apply_migration(migration)

    def _migrations_table_exists(self) -> bool:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
        ).fetchone()
        return row is not None

    def _applied_versions(self) -> set[int]:
        rows = self._conn.execute("SELECT version FROM _migrations").fetchall()
        return {int(r["version"]) for r in rows}

    def _apply_migration(self, migration: _Migration) -> None:
        """Apply a migration outside the normal `_txn` wrapper.

        `sqlite3.executescript` implicitly commits any open transaction
        before running, so wrapping it in BEGIN/COMMIT leaves rollback
        in an unsafe state. We rely on the migrations themselves being
        idempotent (every `CREATE` uses `IF NOT EXISTS`) so a crash
        between the DDL and the tracking-row insert self-heals on the
        next run.
        """
        logger.info("DatadogStore: applying migration %04d_%s", migration.version, migration.name)
        self._conn.executescript(migration.sql)
        self._conn.execute(
            "INSERT OR REPLACE INTO _migrations(version, name, applied_at) VALUES (?,?,?)",
            (migration.version, migration.name, datetime.now(timezone.utc).isoformat()),
        )

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Cursor]:
        """Single transactional unit. Auto-commit/rollback around a block."""
        cur = self._conn.cursor()
        cur.execute("BEGIN")
        try:
            yield cur
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    # ---- fetch_log API -------------------------------------------------

    def record_fetch(
        self,
        *,
        api: str,
        rows_written: int = 0,
        status: FetchStatus = "success",
        error: str = "",
        duration_ms: float = 0.0,
        fetched_at: datetime | None = None,
    ) -> None:
        """Insert one audit row. Call this after every fetch attempt,
        including failures — the failure record is what tells the next run
        to retry instead of trusting a stale success."""
        ts = (fetched_at or datetime.now(timezone.utc)).isoformat()
        with self._txn() as cur:
            cur.execute(
                """
                INSERT INTO fetch_log(api, fetched_at, rows_written, status, error, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (api, ts, rows_written, status, error, duration_ms),
            )

    def last_fetch(self, api: str, *, only_success: bool = True) -> FetchRecord | None:
        """Most recent fetch for `api`. By default only counts successful
        fetches, which is what staleness checks want; pass
        `only_success=False` to also surface failed attempts (e.g. for
        diagnostics)."""
        clauses = ["api = ?"]
        params: list[object] = [api]
        if only_success:
            clauses.append("status = 'success'")
        row = self._conn.execute(
            f"""
            SELECT api, fetched_at, rows_written, status, error, duration_ms
            FROM fetch_log
            WHERE {' AND '.join(clauses)}
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            return None
        return FetchRecord(
            api=row["api"],
            fetched_at=_parse_dt(row["fetched_at"]),
            rows_written=int(row["rows_written"]),
            status=row["status"],
            duration_ms=float(row["duration_ms"]),
            error=row["error"] or "",
        )

    def last_fetched_at(self, api: str) -> datetime | None:
        rec = self.last_fetch(api)
        return rec.fetched_at if rec else None

    def is_stale(
        self,
        api: str,
        ttl_seconds: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        """True if no successful fetch within `ttl_seconds`. Returns True
        when there's no record at all — first-run treated as stale."""
        last = self.last_fetched_at(api)
        if last is None:
            return True
        current = now or datetime.now(timezone.utc)
        return (current - last) > timedelta(seconds=ttl_seconds)

    # ---- introspection -------------------------------------------------

    def fetch_history(self, api: str | None = None, *, limit: int = 50) -> list[FetchRecord]:
        """Return recent fetch rows for diagnostics. `None` = all APIs."""
        if api is None:
            rows = self._conn.execute(
                """
                SELECT api, fetched_at, rows_written, status, error, duration_ms
                FROM fetch_log
                ORDER BY fetched_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT api, fetched_at, rows_written, status, error, duration_ms
                FROM fetch_log WHERE api = ?
                ORDER BY fetched_at DESC LIMIT ?
                """,
                (api, limit),
            ).fetchall()
        return [
            FetchRecord(
                api=r["api"],
                fetched_at=_parse_dt(r["fetched_at"]),
                rows_written=int(r["rows_written"]),
                status=r["status"],
                duration_ms=float(r["duration_ms"]),
                error=r["error"] or "",
            )
            for r in rows
        ]


# ---- module-level helpers --------------------------------------------------


def _discover_migrations() -> list[_Migration]:
    """Load NNNN_*.sql files from this package's `migrations/` directory,
    sorted by version."""
    out: list[_Migration] = []
    pkg = resources.files(__package__) / "migrations"
    for entry in pkg.iterdir():
        if not entry.is_file():
            continue
        match = _MIGRATION_FILE_RE.match(entry.name)
        if not match:
            continue
        version = int(match.group(1))
        name = match.group(2)
        sql = entry.read_text(encoding="utf-8")
        out.append(_Migration(version=version, name=name, sql=sql))
    out.sort(key=lambda m: m.version)
    if out and out[0].version != 1:
        raise RuntimeError("migrations must start at 0001")
    versions = [m.version for m in out]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError(f"non-contiguous migration versions: {versions}")
    return out


def _parse_dt(value: str) -> datetime:
    """Parse a stored ISO timestamp. SQLite gives us strings, not datetimes."""
    return datetime.fromisoformat(value)


# Re-export for callers that just need the latest schema version (e.g. for
# health checks). Computed lazily to avoid import-time filesystem access.
def latest_migration_version() -> int:
    return _discover_migrations()[-1].version
