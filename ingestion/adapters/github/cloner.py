"""Shallow git clones for ingestion.

Wraps `git.Repo` to give us a single high-level operation:

    cloner.clone_or_update(url) -> (Path, sha, changed: bool)

* First call: shallow-clones into `clones_dir/<owner>/<name>`,
  returns (path, head_sha, True).
* Subsequent calls: `git fetch --depth=1 origin` + `git reset --hard
  origin/HEAD`; returns `changed=True` iff the HEAD SHA moved.
* If the on-disk clone is missing or unreadable, we re-clone from scratch.

Auth (private repos): when the optional `token` is provided, the URL is
rewritten to `https://x-access-token:<token>@github.com/...` for the
duration of the clone. The token is NEVER persisted to the on-disk remote
URL — we rewrite `origin` back to the public form after the operation.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloneResult:
    path: Path
    sha: str
    changed: bool
    was_fresh_clone: bool


class RepoCloner:
    def __init__(
        self,
        *,
        clones_dir: Path,
        token: str = "",
    ) -> None:
        self._clones_dir = Path(clones_dir)
        self._token = token

    @property
    def clones_dir(self) -> Path:
        return self._clones_dir

    # ---- public API ----------------------------------------------------

    def target_path(self, owner: str, name: str) -> Path:
        return self._clones_dir / owner / name

    def clone_or_update(
        self,
        *,
        url: str,
        owner: str,
        name: str,
        branch: str = "",
        previous_sha: str = "",
    ) -> CloneResult:
        """Ensure a shallow clone exists at `clones_dir/<owner>/<name>` and
        is up-to-date with the remote.

        `previous_sha` is the SHA we last recorded; if the remote HEAD
        matches it, `changed=False` is returned (the working tree is left
        as-is and no `git reset` is run)."""
        from git import GitCommandError, Repo  # local import: optional dep

        dest = self.target_path(owner, name)
        dest.parent.mkdir(parents=True, exist_ok=True)

        fetch_url = self._auth_url(url)
        public_url = self._strip_auth(url)

        if not _looks_like_git_repo(dest):
            if dest.exists():
                logger.warning("github: %s exists but isn't a git repo; removing", dest)
                shutil.rmtree(dest)
            logger.info("github: cloning %s -> %s", public_url, dest)
            clone_kwargs: dict[str, object] = {"depth": 1}
            if branch:
                clone_kwargs["branch"] = branch
            repo = Repo.clone_from(fetch_url, dest, **clone_kwargs)
            self._scrub_origin(repo, public_url)
            sha = repo.head.commit.hexsha
            return CloneResult(path=dest, sha=sha, changed=True, was_fresh_clone=True)

        repo = Repo(dest)
        self._set_remote(repo, fetch_url)
        try:
            origin = repo.remotes.origin
            origin.fetch(depth=1)
            # `origin/HEAD` may not exist on a shallow clone that pre-dates this
            # logic; fall back to the active branch's upstream.
            try:
                target_ref = "origin/HEAD"
                new_sha = repo.commit(target_ref).hexsha
            except Exception:
                target_ref = f"origin/{repo.active_branch.name}"
                new_sha = repo.commit(target_ref).hexsha
        except GitCommandError as exc:
            self._scrub_origin(repo, public_url)
            raise RuntimeError(f"git fetch failed for {public_url}: {exc}") from exc

        if previous_sha and new_sha == previous_sha:
            self._scrub_origin(repo, public_url)
            return CloneResult(path=dest, sha=new_sha, changed=False, was_fresh_clone=False)

        # SHA moved (or we have no prior SHA recorded) — hard-reset so the
        # working tree matches the new remote tip exactly.
        repo.git.reset("--hard", target_ref)
        self._scrub_origin(repo, public_url)
        return CloneResult(path=dest, sha=new_sha, changed=True, was_fresh_clone=False)

    def remove_clone(self, owner: str, name: str) -> bool:
        """Delete the on-disk clone for `<owner>/<name>`. Returns True if
        something was actually removed."""
        dest = self.target_path(owner, name)
        if dest.exists():
            shutil.rmtree(dest)
            return True
        return False

    def clean_all(self) -> int:
        """Wipe every clone. Returns the count of repos removed."""
        if not self._clones_dir.exists():
            return 0
        removed = 0
        for owner_dir in self._clones_dir.iterdir():
            if not owner_dir.is_dir():
                continue
            for name_dir in owner_dir.iterdir():
                if name_dir.is_dir():
                    shutil.rmtree(name_dir)
                    removed += 1
            with contextlib.suppress(OSError):
                owner_dir.rmdir()  # only succeeds when empty
        return removed

    # ---- url helpers ---------------------------------------------------

    def _auth_url(self, url: str) -> str:
        if not self._token:
            return url
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return url
        netloc = f"x-access-token:{self._token}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))

    @staticmethod
    def _strip_auth(url: str) -> str:
        parsed = urlparse(url)
        if not parsed.hostname:
            return url
        netloc = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
        return urlunparse(parsed._replace(netloc=netloc))

    def _set_remote(self, repo, fetch_url: str) -> None:
        with contextlib.suppress(Exception):
            repo.remotes.origin.set_url(fetch_url)

    def _scrub_origin(self, repo, public_url: str) -> None:
        """Ensure no token-bearing URL is persisted in `.git/config`."""
        with contextlib.suppress(Exception):
            repo.remotes.origin.set_url(public_url)


def _looks_like_git_repo(path: Path) -> bool:
    return (path / ".git").exists()
