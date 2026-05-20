"""Tests for the GitHub metadata SQLite store."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ingestion.adapters.github import GitHubStore

NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def test_migration_runs_once_and_creates_repos_table() -> None:
    store = GitHubStore(":memory:")
    # Re-opening shouldn't double-apply. Migrations are recorded by version.
    versions = store._applied_versions()  # noqa: SLF001
    assert versions == {1}
    store.close()


def test_upsert_and_get_round_trip() -> None:
    store = GitHubStore(":memory:")
    store.upsert_repo(
        url="https://github.com/o/n",
        owner="o",
        name="n",
        clone_path="/tmp/o/n",
        default_branch="main",
        added_at=NOW,
    )
    record = store.get_repo("https://github.com/o/n")
    assert record is not None
    assert record.owner == "o"
    assert record.name == "n"
    assert record.default_branch == "main"
    assert record.clone_path == "/tmp/o/n"
    assert record.status == "registered"
    assert record.last_commit_sha == ""
    assert record.added_at == NOW.isoformat()


def test_upsert_preserves_historical_state() -> None:
    """Re-adding a repo must NOT clobber last_commit_sha / last_ingested_*"""
    store = GitHubStore(":memory:")
    store.upsert_repo(
        url="https://github.com/o/n", owner="o", name="n", clone_path="/a/b"
    )
    store.record_clone("https://github.com/o/n", sha="abc123")
    store.record_ingest("https://github.com/o/n", sha="abc123", at=NOW)

    # Re-add with a different clone_path — historical SHAs survive.
    store.upsert_repo(
        url="https://github.com/o/n", owner="o", name="n", clone_path="/different"
    )
    record = store.get_repo("https://github.com/o/n")
    assert record is not None
    assert record.last_commit_sha == "abc123"
    assert record.last_ingested_sha == "abc123"
    assert record.status == "ingested"
    assert record.clone_path == "/different"


def test_record_clone_advances_sha_but_not_ingest() -> None:
    store = GitHubStore(":memory:")
    store.upsert_repo(url="u", owner="o", name="n", clone_path="/p")
    store.record_clone("u", sha="aaa")
    rec = store.get_repo("u")
    assert rec is not None
    assert rec.last_commit_sha == "aaa"
    assert rec.last_ingested_sha == ""
    assert rec.status == "cloned"


def test_clear_sha_resets_state() -> None:
    store = GitHubStore(":memory:")
    store.upsert_repo(url="u", owner="o", name="n", clone_path="/p")
    store.record_clone("u", sha="aaa")
    store.clear_sha("u")
    rec = store.get_repo("u")
    assert rec is not None
    assert rec.last_commit_sha == ""
    assert rec.status == "registered"


def test_delete_repo() -> None:
    store = GitHubStore(":memory:")
    store.upsert_repo(url="u", owner="o", name="n", clone_path="/p")
    assert store.delete_repo("u") is True
    assert store.delete_repo("u") is False
    assert store.get_repo("u") is None


def test_list_repos_ordered_by_added_at() -> None:
    store = GitHubStore(":memory:")
    earlier = datetime(2026, 5, 18, tzinfo=timezone.utc)
    later = datetime(2026, 5, 20, tzinfo=timezone.utc)
    store.upsert_repo(url="b", owner="o", name="b", clone_path="/b", added_at=later)
    store.upsert_repo(url="a", owner="o", name="a", clone_path="/a", added_at=earlier)
    urls = [r.url for r in store.list_repos()]
    assert urls == ["a", "b"]


def test_mark_error_persists() -> None:
    store = GitHubStore(":memory:")
    store.upsert_repo(url="u", owner="o", name="n", clone_path="/p")
    store.mark_error("u", error="fetch failed: timeout")
    rec = store.get_repo("u")
    assert rec is not None
    assert rec.status == "error"
    assert "timeout" in rec.last_error


def test_disk_backed_store_persists_across_open(tmp_path: Path) -> None:
    db = tmp_path / "github.db"
    store = GitHubStore(db)
    store.upsert_repo(url="u", owner="o", name="n", clone_path="/p")
    store.record_clone("u", sha="zzz")
    store.close()

    reopened = GitHubStore(db)
    rec = reopened.get_repo("u")
    assert rec is not None
    assert rec.last_commit_sha == "zzz"
    reopened.close()
