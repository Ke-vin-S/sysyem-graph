"""SQLite-backed metadata store for GitHub repo clones.

Mirrors `ingestion/adapters/datadog/store.py` — same migration runner
shape (file-based, version-tracked, `CREATE … IF NOT EXISTS`-idempotent),
same WAL + autocommit, `:memory:` default for tests.

The `repos` table tracks one row per registered repository:
  * `url`              — canonical `https://github.com/owner/name`
  * `clone_path`       — where the working copy lives on disk
  * `last_commit_sha`  — SHA we observed at last fetch (clone or update)
  * `last_ingested_*`  — SHA + timestamp of the last successful ingest;
                        the adapter compares against `last_commit_sha`
                        to decide whether to skip
  * `status`           — `registered` | `cloned` | `ingested` | `error`

The store is single-threaded and not safe to share across processes.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)

_MIGRATION_FILE_RE = re.compile(r"^(\d{4})_([a-zA-Z0-9_]+)\.sql$")


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    sql: str


@dataclass(frozen=True)
class RepoRecord:
    url: str
    owner: str
    name: str
    default_branch: str
    clone_path: str
    last_commit_sha: str
    last_ingested_at: str
    last_ingested_sha: str
    status: str
    last_error: str
    added_at: str


class GitHubStore:
    """SQLite metadata store for registered GitHub repositories.

    Usage:
        with GitHubStore("./out/github.db") as store:
            store.upsert_repo(url=..., owner=..., name=..., clone_path=...)
            store.record_clone(url, sha="abc123")
            store.record_ingest(url, sha="abc123", at=datetime.now(timezone.utc))

    Pass `":memory:"` (the default) for tests.
    """

    # Tables every fully-initialised GitHubStore must have. Checked at the
    # end of `_run_migrations` so a half-initialised on-disk file (e.g.
    # one created by an earlier process before the migrations dir was
    # present on the import path) self-heals on the next open instead of
    # producing cryptic "no such table: repos" errors.
    _REQUIRED_TABLES: tuple[str, ...] = ("repos",)

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._run_migrations()

    # ---- lifecycle -----------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> GitHubStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def path(self) -> str:
        return self._path

    # ---- migrations ----------------------------------------------------

    def _run_migrations(self) -> None:
        migrations = _discover_migrations()
        if not migrations:
            # Packaging bug or wrong cwd: migrations dir is empty but the
            # store is being asked to provision a schema. Surface it loud
            # instead of silently leaving a half-built DB on disk.
            if not self._has_required_schema():
                raise RuntimeError(
                    "GitHubStore: no migration files discovered AND required "
                    f"tables {self._REQUIRED_TABLES} are missing from "
                    f"{self._path!r}. The package may be installed without "
                    "its migration data; reinstall with `pip install -e .` "
                    "from the repo root."
                )
            return
        if not self._migrations_table_exists():
            self._apply_migration(migrations[0])
            migrations = migrations[1:]
        applied = self._applied_versions()
        for migration in migrations:
            if migration.version in applied:
                continue
            self._apply_migration(migration)
        # Self-heal: if everything the tracker says is applied but the
        # schema is still missing required tables (e.g. an aborted prior
        # init left an empty file behind, or the tracker is somehow
        # populated against a DB that has no other tables), re-run the
        # baseline. Every CREATE in migrations[0] uses `IF NOT EXISTS`,
        # so this is safe.
        if not self._has_required_schema():
            logger.warning(
                "GitHubStore: %r missing required tables after migration "
                "run — re-applying baseline migration 0001",
                self._path,
            )
            self._apply_migration(_discover_migrations()[0])
            if not self._has_required_schema():
                raise RuntimeError(
                    f"GitHubStore: required tables {self._REQUIRED_TABLES} "
                    "still missing after re-applying baseline migration. "
                    f"Inspect {self._path!r} manually or delete it to "
                    "force a clean rebuild."
                )

    def _migrations_table_exists(self) -> bool:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
        ).fetchone()
        return row is not None

    def _has_required_schema(self) -> bool:
        """True iff every table in `_REQUIRED_TABLES` exists in the DB."""
        if not self._REQUIRED_TABLES:
            return True
        placeholders = ",".join("?" * len(self._REQUIRED_TABLES))
        row = self._conn.execute(
            f"SELECT count(name) FROM sqlite_master "
            f"WHERE type='table' AND name IN ({placeholders})",
            self._REQUIRED_TABLES,
        ).fetchone()
        return int(row[0]) == len(self._REQUIRED_TABLES)

    def _applied_versions(self) -> set[int]:
        rows = self._conn.execute("SELECT version FROM _migrations").fetchall()
        return {int(r["version"]) for r in rows}

    def _apply_migration(self, migration: _Migration) -> None:
        # `executescript` commits any open txn before running; we keep
        # migration application outside `_txn` and rely on `IF NOT EXISTS`
        # idempotency for crash recovery (same approach as DatadogStore).
        logger.info("GitHubStore: applying migration %04d_%s", migration.version, migration.name)
        self._conn.executescript(migration.sql)
        self._conn.execute(
            "INSERT OR REPLACE INTO _migrations(version, name, applied_at) VALUES (?,?,?)",
            (migration.version, migration.name, datetime.now(timezone.utc).isoformat()),
        )

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        cur.execute("BEGIN")
        try:
            yield cur
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    # ---- repo CRUD -----------------------------------------------------

    def upsert_repo(
        self,
        *,
        url: str,
        owner: str,
        name: str,
        clone_path: str,
        default_branch: str = "",
        added_at: datetime | None = None,
    ) -> None:
        """Register a repo. If it already exists, only the mutable fields
        (clone_path, default_branch) get refreshed — historical state
        (last_commit_sha, last_ingested_*) is preserved."""
        ts = (added_at or datetime.now(timezone.utc)).isoformat()
        with self._txn() as cur:
            cur.execute(
                """
                INSERT INTO repos(
                    url, owner, name, default_branch, clone_path,
                    status, added_at
                ) VALUES (?, ?, ?, ?, ?, 'registered', ?)
                ON CONFLICT(url) DO UPDATE SET
                    owner = excluded.owner,
                    name  = excluded.name,
                    default_branch = excluded.default_branch,
                    clone_path = excluded.clone_path
                """,
                (url, owner, name, default_branch, clone_path, ts),
            )

    def get_repo(self, url: str) -> RepoRecord | None:
        row = self._conn.execute(
            """
            SELECT url, owner, name, default_branch, clone_path,
                   last_commit_sha, last_ingested_at, last_ingested_sha,
                   status, last_error, added_at
            FROM repos WHERE url = ?
            """,
            (url,),
        ).fetchone()
        return _row_to_record(row) if row else None

    def list_repos(self) -> list[RepoRecord]:
        rows = self._conn.execute(
            """
            SELECT url, owner, name, default_branch, clone_path,
                   last_commit_sha, last_ingested_at, last_ingested_sha,
                   status, last_error, added_at
            FROM repos ORDER BY added_at ASC
            """
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def delete_repo(self, url: str) -> bool:
        with self._txn() as cur:
            cur.execute("DELETE FROM repos WHERE url = ?", (url,))
            return cur.rowcount > 0

    def record_clone(self, url: str, *, sha: str) -> None:
        """Mark a repo as freshly cloned/updated. `last_commit_sha` advances
        but `last_ingested_*` are untouched — the SHA delta is what the
        adapter uses to decide whether re-ingest is needed."""
        with self._txn() as cur:
            cur.execute(
                """
                UPDATE repos
                SET last_commit_sha = ?, status = 'cloned', last_error = ''
                WHERE url = ?
                """,
                (sha, url),
            )

    def record_ingest(self, url: str, *, sha: str, at: datetime) -> None:
        with self._txn() as cur:
            cur.execute(
                """
                UPDATE repos
                SET last_ingested_at = ?, last_ingested_sha = ?,
                    status = 'ingested', last_error = ''
                WHERE url = ?
                """,
                (at.isoformat(), sha, url),
            )

    def mark_error(self, url: str, *, error: str) -> None:
        with self._txn() as cur:
            cur.execute(
                "UPDATE repos SET status = 'error', last_error = ? WHERE url = ?",
                (error, url),
            )

    def clear_sha(self, url: str) -> None:
        """Forget the last-cloned SHA. Used by `clean` so the next ingest
        re-clones from scratch even when the clone dir survived."""
        with self._txn() as cur:
            cur.execute(
                """
                UPDATE repos
                SET last_commit_sha = NULL, status = 'registered'
                WHERE url = ?
                """,
                (url,),
            )


# ---- helpers --------------------------------------------------------------


def _row_to_record(row: sqlite3.Row) -> RepoRecord:
    return RepoRecord(
        url=row["url"],
        owner=row["owner"],
        name=row["name"],
        default_branch=row["default_branch"] or "",
        clone_path=row["clone_path"],
        last_commit_sha=row["last_commit_sha"] or "",
        last_ingested_at=row["last_ingested_at"] or "",
        last_ingested_sha=row["last_ingested_sha"] or "",
        status=row["status"],
        last_error=row["last_error"] or "",
        added_at=row["added_at"],
    )


def _discover_migrations() -> list[_Migration]:
    out: list[_Migration] = []
    pkg = resources.files(__package__) / "migrations"
    for entry in pkg.iterdir():
        if not entry.is_file():
            continue
        match = _MIGRATION_FILE_RE.match(entry.name)
        if not match:
            continue
        version = int(match.group(1))
        sql = entry.read_text(encoding="utf-8")
        out.append(_Migration(version=version, name=match.group(2), sql=sql))
    out.sort(key=lambda m: m.version)
    if out and out[0].version != 1:
        raise RuntimeError("migrations must start at 0001")
    versions = [m.version for m in out]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError(f"non-contiguous migration versions: {versions}")
    return out
