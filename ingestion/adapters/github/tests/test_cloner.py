"""Tests for RepoCloner using a local bare-repo as the remote.

No network access — all git operations run against `tmp_path/remote.git`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ingestion.adapters.github import RepoCloner

pytest.importorskip("git", reason="GitPython not installed")


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _make_bare_remote(tmp_path: Path) -> tuple[Path, str]:
    """Build a bare 'remote' with one initial commit. Returns (url, sha)."""
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

    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=work, text=True
    ).strip()
    return remote, sha


def _push_new_commit(remote_url: Path, tmp_path: Path, filename: str = "extra.py") -> str:
    work = tmp_path / "_seed2"
    work.mkdir()
    _run("git", "clone", str(remote_url), str(work), cwd=tmp_path)
    _run("git", "config", "user.email", "t@t", cwd=work)
    _run("git", "config", "user.name", "t", cwd=work)
    (work / filename).write_text(f"# {filename}\n")
    _run("git", "add", ".", cwd=work)
    _run("git", "commit", "-m", f"add {filename}", cwd=work)
    _run("git", "push", "origin", "main", cwd=work)
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=work, text=True
    ).strip()
    return sha


def test_first_call_clones_and_returns_changed(tmp_path: Path) -> None:
    remote, sha = _make_bare_remote(tmp_path)
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    result = cloner.clone_or_update(
        url=str(remote), owner="acme", name="thing", branch="main"
    )
    assert result.changed is True
    assert result.was_fresh_clone is True
    assert result.sha == sha
    assert (result.path / ".git").exists()
    assert (result.path / "hello.py").exists()


def test_second_call_no_remote_movement_returns_unchanged(tmp_path: Path) -> None:
    remote, sha = _make_bare_remote(tmp_path)
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    first = cloner.clone_or_update(
        url=str(remote), owner="acme", name="thing", branch="main"
    )
    second = cloner.clone_or_update(
        url=str(remote),
        owner="acme",
        name="thing",
        branch="main",
        previous_sha=first.sha,
    )
    assert second.changed is False
    assert second.was_fresh_clone is False
    assert second.sha == sha


def test_third_call_after_new_commit_returns_changed(tmp_path: Path) -> None:
    remote, sha1 = _make_bare_remote(tmp_path)
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    first = cloner.clone_or_update(
        url=str(remote), owner="acme", name="thing", branch="main"
    )

    sha2 = _push_new_commit(remote, tmp_path)
    assert sha2 != sha1

    third = cloner.clone_or_update(
        url=str(remote),
        owner="acme",
        name="thing",
        branch="main",
        previous_sha=first.sha,
    )
    assert third.changed is True
    assert third.was_fresh_clone is False
    assert third.sha == sha2
    assert (third.path / "extra.py").exists()


def test_re_clones_when_local_dir_is_not_a_git_repo(tmp_path: Path) -> None:
    remote, sha = _make_bare_remote(tmp_path)
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    # Pre-create a bogus directory at the target path.
    target = cloner.target_path("acme", "thing")
    target.mkdir(parents=True)
    (target / "garbage.txt").write_text("not a repo")

    result = cloner.clone_or_update(
        url=str(remote), owner="acme", name="thing", branch="main"
    )
    assert result.was_fresh_clone is True
    assert result.sha == sha
    assert not (target / "garbage.txt").exists()


def test_remove_clone(tmp_path: Path) -> None:
    remote, _ = _make_bare_remote(tmp_path)
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    cloner.clone_or_update(url=str(remote), owner="o", name="n", branch="main")
    assert cloner.remove_clone("o", "n") is True
    assert cloner.remove_clone("o", "n") is False  # idempotent


def test_clean_all_wipes_every_clone(tmp_path: Path) -> None:
    remote, _ = _make_bare_remote(tmp_path)
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    cloner.clone_or_update(url=str(remote), owner="o", name="a", branch="main")
    cloner.clone_or_update(url=str(remote), owner="o", name="b", branch="main")
    assert cloner.clean_all() == 2
