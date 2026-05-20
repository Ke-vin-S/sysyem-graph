"""End-to-end lifecycle tests for GitHubService.

Uses a local bare-repo as the remote — no network. Walks the full
add → ensure_fresh → record_ingest → ensure_fresh (cached) → remove flow.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.types.errors import IngestionError
from ingestion.adapters.github import (
    GitHubService,
    GitHubStore,
    RepoCloner,
    normalize_repo_url,
    parse_owner_name,
)

pytest.importorskip("git", reason="GitPython not installed")

NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _make_bare_remote(tmp_path: Path) -> tuple[Path, str]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _run("git", "init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    work = tmp_path / "_seed"
    work.mkdir()
    _run("git", "init", "-b", "main", cwd=work)
    _run("git", "config", "user.email", "t@t", cwd=work)
    _run("git", "config", "user.name", "t", cwd=work)
    (work / "hello.py").write_text("def hi():\n    return 1\n")
    _run("git", "add", ".", cwd=work)
    _run("git", "commit", "-m", "initial", cwd=work)
    _run("git", "remote", "add", "origin", str(remote), cwd=work)
    _run("git", "push", "origin", "main", cwd=work)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=work, text=True).strip()
    return remote, sha


def _push_commit(remote: Path, tmp_path: Path, name: str = "extra.py") -> str:
    work = tmp_path / "_push"
    work.mkdir()
    _run("git", "clone", str(remote), str(work), cwd=tmp_path)
    _run("git", "config", "user.email", "t@t", cwd=work)
    _run("git", "config", "user.name", "t", cwd=work)
    (work / name).write_text("# new\n")
    _run("git", "add", ".", cwd=work)
    _run("git", "commit", "-m", f"add {name}", cwd=work)
    _run("git", "push", "origin", "main", cwd=work)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=work, text=True
    ).strip()


@pytest.fixture
def service(tmp_path: Path) -> GitHubService:
    store = GitHubStore(":memory:")
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    return GitHubService(store=store, cloner=cloner)


# ---- URL helpers ----------------------------------------------------------


def test_normalize_url_accepts_short_form() -> None:
    assert normalize_repo_url("acme/thing") == "https://github.com/acme/thing"


def test_normalize_url_strips_dot_git() -> None:
    assert (
        normalize_repo_url("https://github.com/acme/thing.git")
        == "https://github.com/acme/thing"
    )


def test_parse_owner_name() -> None:
    assert parse_owner_name("https://github.com/acme/thing") == ("acme", "thing")


def test_normalize_url_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        normalize_repo_url("bareword")


# ---- registry -------------------------------------------------------------


def test_add_repo_registers_with_canonical_url(service: GitHubService) -> None:
    rec = service.add_repo("acme/thing")
    assert rec.url == "https://github.com/acme/thing"
    assert rec.owner == "acme"
    assert rec.name == "thing"
    assert rec.status == "registered"


def test_add_repo_is_idempotent(service: GitHubService) -> None:
    service.add_repo("acme/thing")
    service.add_repo("acme/thing", branch="develop")  # update branch
    repos = service.list_repos()
    assert len(repos) == 1
    assert repos[0].default_branch == "develop"


def test_remove_repo_returns_false_when_unknown(service: GitHubService) -> None:
    assert service.remove_repo("acme/missing") is False


# ---- ensure_fresh ---------------------------------------------------------


def test_ensure_fresh_requires_registration(service: GitHubService) -> None:
    with pytest.raises(IngestionError):
        service.ensure_fresh("https://github.com/acme/missing")


def test_ensure_fresh_full_lifecycle(tmp_path: Path, service: GitHubService) -> None:
    remote, sha1 = _make_bare_remote(tmp_path)

    # 1. Register the bare-repo path as the URL. Pass explicit owner/name
    # because the filesystem path doesn't carry GitHub `owner/name` semantics.
    service.add_repo(str(remote), owner="acme", name="thing")
    repos = service.list_repos()
    url = repos[0].url

    # 2. First fetch — fresh clone, status=cloned, SHA recorded.
    first = service.ensure_fresh(url, now=NOW)
    assert first.was_stale is True
    assert first.was_fresh_clone is True
    assert first.sha == sha1
    rec = service.get_repo(url)
    assert rec is not None
    assert rec.status == "cloned"
    assert rec.last_commit_sha == sha1
    assert rec.last_ingested_sha == ""

    # 3. Mark as ingested at this SHA. is_ingested then matches.
    service.record_ingest(url, sha=sha1, at=NOW)
    assert service.is_ingested(url, sha1) is True
    rec = service.get_repo(url)
    assert rec is not None
    assert rec.status == "ingested"

    # 4. Re-run with no remote movement → was_stale=False.
    second = service.ensure_fresh(url, now=NOW)
    assert second.was_stale is False
    assert second.sha == sha1

    # 5. Push a new commit upstream → re-run is stale and is_ingested False.
    sha2 = _push_commit(remote, tmp_path)
    third = service.ensure_fresh(url, now=NOW)
    assert third.was_stale is True
    assert third.sha == sha2
    assert service.is_ingested(url, sha2) is False

    # 6. clean_clones wipes the working tree AND resets last_commit_sha.
    service.clean_clones(url=url)
    rec = service.get_repo(url)
    assert rec is not None
    assert rec.last_commit_sha == ""
    assert not Path(rec.clone_path).exists()


def test_get_status_reports_disk_and_ingest_state(
    tmp_path: Path, service: GitHubService
) -> None:
    remote, _ = _make_bare_remote(tmp_path)
    service.add_repo(str(remote), owner="acme", name="thing")
    url = service.list_repos()[0].url
    statuses = service.get_status()
    assert len(statuses) == 1
    assert statuses[0].clone_exists is False
    assert statuses[0].needs_ingest is True

    service.ensure_fresh(url, now=NOW)
    after = service.get_status()
    assert after[0].clone_exists is True
    assert after[0].clone_size_bytes > 0
