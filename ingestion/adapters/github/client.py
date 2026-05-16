"""Thin wrapper around PyGithub.

We only need a handful of operations (get repo metadata, walk the default
branch tree, read file contents). PyGithub's pagination is automatic; the
wrapper translates its responses into plain dataclasses for the rest of the
pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.types.errors import IngestionError

logger = logging.getLogger(__name__)


@dataclass
class RepoFile:
    path: str
    size: int
    sha: str
    content: str | None = None


@dataclass
class RepoSnapshot:
    full_name: str
    default_branch: str
    clone_url: str
    language: str | None
    description: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pushed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    files: list[RepoFile] = field(default_factory=list)
    owner: str = ""


class GitHubClient:
    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        *,
        _github_factory: Any | None = None,
    ) -> None:
        self._token = token
        self._api_url = api_url
        self._github_factory = _github_factory
        self._gh: Any | None = None

    def _gh_client(self) -> Any:
        if self._gh is not None:
            return self._gh
        if self._github_factory is not None:
            self._gh = self._github_factory()
            return self._gh
        from github import Auth, Github

        self._gh = Github(auth=Auth.Token(self._token), base_url=self._api_url)
        return self._gh

    def fetch_repo(
        self,
        full_name: str,
        *,
        file_extensions: tuple[str, ...],
        max_file_bytes: int,
    ) -> RepoSnapshot:
        try:
            repo = self._gh_client().get_repo(full_name)
        except Exception as exc:  # pragma: no cover - network/auth errors
            raise IngestionError("github", f"get_repo failed for {full_name}", cause=exc) from exc

        try:
            tree = repo.get_git_tree(repo.default_branch, recursive=True)
        except Exception as exc:  # pragma: no cover
            raise IngestionError(
                "github", f"get_git_tree failed for {full_name}", cause=exc
            ) from exc

        files: list[RepoFile] = []
        for entry in self._iter_tree_entries(tree):
            if entry["type"] != "blob":
                continue
            path = entry["path"]
            if not path.endswith(file_extensions):
                continue
            size = int(entry.get("size") or 0)
            if size > max_file_bytes:
                continue
            content = self._read_file(repo, path)
            files.append(RepoFile(path=path, size=size, sha=entry["sha"], content=content))

        return RepoSnapshot(
            full_name=full_name,
            default_branch=repo.default_branch,
            clone_url=getattr(repo, "clone_url", "") or "",
            language=getattr(repo, "language", None),
            description=getattr(repo, "description", "") or "",
            created_at=_safe_datetime(getattr(repo, "created_at", None)),
            pushed_at=_safe_datetime(getattr(repo, "pushed_at", None)),
            owner=full_name.split("/", 1)[0],
            files=files,
        )

    def _iter_tree_entries(self, tree: Any) -> Iterator[dict[str, Any]]:
        for entry in getattr(tree, "tree", []) or []:
            yield {
                "path": getattr(entry, "path", ""),
                "type": getattr(entry, "type", ""),
                "sha": getattr(entry, "sha", ""),
                "size": getattr(entry, "size", None),
            }

    def _read_file(self, repo: Any, path: str) -> str | None:
        try:
            content_file = repo.get_contents(path)
        except Exception:
            logger.warning("github: failed to read %s/%s", repo.full_name, path)
            return None
        # get_contents can return a list for directories — paranoid check.
        if isinstance(content_file, list):
            return None
        decoded = getattr(content_file, "decoded_content", None)
        if decoded is None:
            return None
        try:
            return decoded.decode("utf-8", errors="replace")
        except AttributeError:
            return str(decoded)


def _safe_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)
