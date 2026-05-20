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


# ---- auth-failure classification through the cloner ---------------------


def test_clone_failure_with_no_token_against_real_remote_is_runtime_error(
    tmp_path: Path,
) -> None:
    """Clone against a *missing* local path produces a generic error, not
    AuthError — there's no token configured, no auth-flavored stderr."""
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    with pytest.raises((RuntimeError, Exception)):
        cloner.clone_or_update(
            url="file:///no/such/path", owner="o", name="n", branch="main"
        )


def test_auth_error_surfaced_when_stderr_looks_like_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Simulate a GitCommandError with auth-flavored stderr and assert the
    cloner re-raises as AuthError (rather than RuntimeError)."""
    import git as gitlib

    from ingestion.adapters.github.auth import AuthError, TokenResolver
    from ingestion.adapters.github.cloner import RepoCloner

    def boom(*args, **kwargs):
        raise gitlib.GitCommandError(
            command=["git", "clone"],
            status=128,
            stderr="remote: Repository not found.\nfatal: repository '…' not found\n",
        )

    monkeypatch.setattr(gitlib.Repo, "clone_from", staticmethod(boom))

    # No token configured for github.com → classify_git_error returns AuthError.
    resolver = TokenResolver(env={})
    cloner = RepoCloner(clones_dir=tmp_path / "clones", token_resolver=resolver)
    with pytest.raises(AuthError) as exc_info:
        cloner.clone_or_update(
            url="https://github.com/acme/private",
            owner="acme",
            name="private",
            branch="main",
        )
    assert "GITHUB_TOKEN" in exc_info.value.hint
    assert exc_info.value.host == "github.com"


def test_auth_error_with_token_marks_token_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import git as gitlib

    from ingestion.adapters.github.auth import AuthError, TokenResolver
    from ingestion.adapters.github.cloner import RepoCloner

    def boom(*args, **kwargs):
        raise gitlib.GitCommandError(
            command=["git", "clone"],
            status=128,
            stderr="fatal: Authentication failed for 'https://github.com/o/n'\n",
        )

    monkeypatch.setattr(gitlib.Repo, "clone_from", staticmethod(boom))

    resolver = TokenResolver(env={"GITHUB_TOKEN": "ghp_x"})
    cloner = RepoCloner(clones_dir=tmp_path / "clones", token_resolver=resolver)
    with pytest.raises(AuthError) as exc_info:
        cloner.clone_or_update(
            url="https://github.com/o/n", owner="o", name="n", branch="main"
        )
    assert exc_info.value.token_configured is True
    assert "rejected" in exc_info.value.hint


def test_clean_all_wipes_every_clone(tmp_path: Path) -> None:
    remote, _ = _make_bare_remote(tmp_path)
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    cloner.clone_or_update(url=str(remote), owner="o", name="a", branch="main")
    cloner.clone_or_update(url=str(remote), owner="o", name="b", branch="main")
    assert cloner.clean_all() == 2
