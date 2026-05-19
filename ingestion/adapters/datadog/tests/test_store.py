"""Unit tests for the DatadogStore SQLite staging layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ingestion.adapters.datadog.store import DatadogStore, latest_migration_version

NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


# ---- migration runner ------------------------------------------------------


def test_migrations_apply_on_empty_db() -> None:
    with DatadogStore(":memory:") as store:
        # `fetch_log` must exist after init.
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fetch_log'"
        ).fetchall()
        assert rows, "fetch_log table missing — migrations didn't run"


def test_migrations_record_their_version() -> None:
    with DatadogStore(":memory:") as store:
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT version, name FROM _migrations ORDER BY version"
        ).fetchall()
        assert rows
        assert rows[0]["version"] == 1
        assert rows[0]["name"] == "init"
        # Whatever the latest version is, every prior one must be present.
        versions = [r["version"] for r in rows]
        assert versions == list(range(1, latest_migration_version() + 1))


def test_migrations_idempotent_across_runs(tmp_path: Path) -> None:
    db = tmp_path / "datadog.db"
    with DatadogStore(db) as a:
        a.record_fetch(api="spans", rows_written=10)
    # Re-open: migrations should not re-run (no duplicate _migrations rows,
    # no error) and existing data should still be there.
    with DatadogStore(db) as b:
        rec = b.last_fetch("spans")
        assert rec is not None and rec.rows_written == 10
        rows = b._conn.execute("SELECT COUNT(*) AS n FROM _migrations").fetchone()  # noqa: SLF001
        assert rows["n"] == latest_migration_version()


def test_store_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "datadog.db"
    DatadogStore(nested).close()
    assert nested.exists()


# ---- fetch_log API ---------------------------------------------------------


def test_record_fetch_and_read_back() -> None:
    with DatadogStore(":memory:") as store:
        store.record_fetch(
            api="spans",
            rows_written=1247,
            duration_ms=512.3,
            fetched_at=NOW,
        )
        rec = store.last_fetch("spans")
        assert rec is not None
        assert rec.api == "spans"
        assert rec.rows_written == 1247
        assert rec.duration_ms == pytest.approx(512.3)
        assert rec.status == "success"
        assert rec.fetched_at == NOW


def test_last_fetch_returns_none_when_empty() -> None:
    with DatadogStore(":memory:") as store:
        assert store.last_fetch("spans") is None
        assert store.last_fetched_at("spans") is None


def test_last_fetch_picks_most_recent_success() -> None:
    with DatadogStore(":memory:") as store:
        store.record_fetch(api="spans", rows_written=10, fetched_at=NOW - timedelta(hours=2))
        store.record_fetch(api="spans", rows_written=20, fetched_at=NOW - timedelta(hours=1))
        store.record_fetch(api="spans", rows_written=30, fetched_at=NOW)
        rec = store.last_fetch("spans")
        assert rec is not None
        assert rec.rows_written == 30
        assert rec.fetched_at == NOW


def test_last_fetch_default_skips_failures() -> None:
    """A failed pull must NOT mask the prior successful one for TTL purposes."""
    with DatadogStore(":memory:") as store:
        store.record_fetch(api="spans", rows_written=99, fetched_at=NOW - timedelta(hours=1))
        store.record_fetch(
            api="spans",
            rows_written=0,
            status="failed",
            error="429 rate limit",
            fetched_at=NOW,
        )
        # Default: only_success=True
        rec = store.last_fetch("spans")
        assert rec is not None
        assert rec.status == "success"
        assert rec.rows_written == 99
        # Opt in to seeing the failure too.
        rec_all = store.last_fetch("spans", only_success=False)
        assert rec_all is not None
        assert rec_all.status == "failed"
        assert rec_all.error == "429 rate limit"


def test_record_fetch_rejects_invalid_status() -> None:
    with DatadogStore(":memory:") as store:
        with pytest.raises(Exception):  # noqa: BLE001 - sqlite raises IntegrityError
            store.record_fetch(api="spans", status="bogus")  # type: ignore[arg-type]


# ---- staleness -------------------------------------------------------------


def test_is_stale_when_never_fetched() -> None:
    with DatadogStore(":memory:") as store:
        assert store.is_stale("catalog", ttl_seconds=3600) is True


def test_is_stale_within_ttl_returns_false() -> None:
    with DatadogStore(":memory:") as store:
        store.record_fetch(api="catalog", rows_written=42, fetched_at=NOW - timedelta(minutes=10))
        assert store.is_stale("catalog", ttl_seconds=3600, now=NOW) is False


def test_is_stale_after_ttl_returns_true() -> None:
    with DatadogStore(":memory:") as store:
        store.record_fetch(api="catalog", rows_written=42, fetched_at=NOW - timedelta(hours=2))
        assert store.is_stale("catalog", ttl_seconds=3600, now=NOW) is True


def test_is_stale_only_counts_successful_fetches() -> None:
    """A recent failure doesn't refresh the TTL clock."""
    with DatadogStore(":memory:") as store:
        store.record_fetch(
            api="catalog", rows_written=42, fetched_at=NOW - timedelta(hours=2)
        )
        # Fresh failure: must NOT count as a successful refresh.
        store.record_fetch(
            api="catalog", status="failed", error="500", fetched_at=NOW
        )
        assert store.is_stale("catalog", ttl_seconds=3600, now=NOW) is True


# ---- introspection ---------------------------------------------------------


def test_fetch_history_orders_newest_first() -> None:
    with DatadogStore(":memory:") as store:
        store.record_fetch(api="spans", rows_written=1, fetched_at=NOW - timedelta(hours=3))
        store.record_fetch(api="spans", rows_written=2, fetched_at=NOW - timedelta(hours=1))
        store.record_fetch(api="catalog", rows_written=5, fetched_at=NOW - timedelta(hours=2))
        history = store.fetch_history()
        assert [r.rows_written for r in history] == [2, 5, 1]


def test_fetch_history_filters_by_api() -> None:
    with DatadogStore(":memory:") as store:
        store.record_fetch(api="spans", rows_written=1, fetched_at=NOW - timedelta(hours=1))
        store.record_fetch(api="catalog", rows_written=5, fetched_at=NOW)
        spans_only = store.fetch_history(api="spans")
        assert {r.api for r in spans_only} == {"spans"}


# ---- lifecycle / context manager ------------------------------------------


def test_context_manager_closes_connection() -> None:
    with DatadogStore(":memory:") as store:
        store.record_fetch(api="spans")
    # After __exit__, further operations should raise (connection closed).
    with pytest.raises(Exception):  # noqa: BLE001
        store.record_fetch(api="spans")
