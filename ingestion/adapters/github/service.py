"""GitHubService — high-level operations for the GitHub ingestor.

This is the layer the CLI (and a future web UI) call into. It composes
`GitHubStore` (metadata) and `RepoCloner` (filesystem) and exposes the
repo lifecycle:

    service.add_repo(url)
    service.list_repos()
    service.remove_repo(url)
    service.clean_clones(url=None)        # wipe clone, keep DB row
    service.ensure_fresh(url, now=...)    # clone-or-update; returns SHA
    service.record_ingest(url, sha, now)  # commit success
    service.get_status()                  # rich snapshot for `github status`

The split between `ensure_fresh` (does git work + advances
`last_commit_sha`) and `record_ingest` (advances `last_ingested_sha`) is
intentional: the adapter only marks a repo as ingested AFTER it has
successfully built records, so a crash mid-extract leaves
`last_ingested_sha != last_commit_sha` and the next run retries.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from core.types.errors import IngestionError
from ingestion.adapters.github.auth import AuthError
from ingestion.adapters.github.cloner import CloneResult, RepoCloner
from ingestion.adapters.github.store import GitHubStore, RepoRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FreshResult:
    """Outcome of `ensure_fresh`. `was_stale=True` means we cloned or
    fast-forwarded; `False` means the remote hadn't moved since
    `last_commit_sha` and the working tree is untouched."""

    path: Path
    sha: str
    was_stale: bool
    was_fresh_clone: bool


@dataclass(frozen=True)
class RepoStatus:
    record: RepoRecord
    clone_exists: bool
    clone_size_bytes: int
    needs_ingest: bool


class GitHubService:
    def __init__(self, store: GitHubStore, cloner: RepoCloner) -> None:
        self._store = store
        self._cloner = cloner

    # ---- registry ------------------------------------------------------

    def add_repo(
        self,
        url: str,
        *,
        branch: str = "",
        owner: str = "",
        name: str = "",
    ) -> RepoRecord:
        """Register a repo. Idempotent — re-adding refreshes the
        default_branch + clone_path but preserves historical SHAs.

        `owner` and `name` are usually derived from the URL. Pass them
        explicitly when the URL doesn't carry standard `owner/name`
        semantics (e.g. tests using local bare repositories)."""
        canonical = normalize_repo_url(url)
        if not owner or not name:
            owner, name = parse_owner_name(canonical)
        clone_path = self._cloner.target_path(owner, name)
        self._store.upsert_repo(
            url=canonical,
            owner=owner,
            name=name,
            clone_path=str(clone_path),
            default_branch=branch,
        )
        record = self._store.get_repo(canonical)
        assert record is not None  # we literally just inserted it
        return record

    def list_repos(self) -> list[RepoRecord]:
        return self._store.list_repos()

    def get_repo(self, url: str) -> RepoRecord | None:
        return self._store.get_repo(normalize_repo_url(url))

    def remove_repo(self, url: str, *, delete_clone: bool = True) -> bool:
        canonical = normalize_repo_url(url)
        record = self._store.get_repo(canonical)
        if record is None:
            return False
        if delete_clone:
            self._cloner.remove_clone(record.owner, record.name)
        self._store.delete_repo(canonical)
        return True

    def clean_clones(self, url: str | None = None) -> int:
        """Wipe on-disk clones but keep DB rows. The next `ensure_fresh`
        will re-clone from scratch. Returns count removed."""
        if url is None:
            removed = self._cloner.clean_all()
            for r in self._store.list_repos():
                self._store.clear_sha(r.url)
            return removed
        canonical = normalize_repo_url(url)
        record = self._store.get_repo(canonical)
        if record is None:
            return 0
        if self._cloner.remove_clone(record.owner, record.name):
            self._store.clear_sha(canonical)
            return 1
        return 0

    # ---- incremental update --------------------------------------------

    def ensure_fresh(self, url: str, *, now: datetime | None = None) -> FreshResult:
        """Clone or fast-forward `url`. Caller must have called
        `add_repo(url)` first; we raise IngestionError otherwise."""
        canonical = normalize_repo_url(url)
        record = self._store.get_repo(canonical)
        if record is None:
            raise IngestionError(
                "github",
                f"{canonical} not registered — run `sg-ingest github add` first",
            )

        try:
            result: CloneResult = self._cloner.clone_or_update(
                url=canonical,
                owner=record.owner,
                name=record.name,
                branch=record.default_branch,
                previous_sha=record.last_commit_sha,
            )
        except AuthError as exc:
            # Record the doctor message in the DB and re-raise unchanged so the
            # adapter/CLI can render a friendly hint instead of a stack trace.
            self._store.mark_error(canonical, error=str(exc))
            raise
        except Exception as exc:
            self._store.mark_error(canonical, error=str(exc))
            raise IngestionError("github", f"clone/update failed for {canonical}: {exc}") from exc

        # Always record the observed SHA; status -> 'cloned'.
        self._store.record_clone(canonical, sha=result.sha)
        del now  # currently unused; reserved for future TTL-style features

        return FreshResult(
            path=result.path,
            sha=result.sha,
            was_stale=result.changed,
            was_fresh_clone=result.was_fresh_clone,
        )

    def is_ingested(self, url: str, sha: str) -> bool:
        """True if `url` was successfully ingested at this exact SHA."""
        canonical = normalize_repo_url(url)
        record = self._store.get_repo(canonical)
        if record is None:
            return False
        return bool(record.last_ingested_sha) and record.last_ingested_sha == sha

    def record_ingest(
        self,
        url: str,
        *,
        sha: str,
        at: datetime | None = None,
    ) -> None:
        canonical = normalize_repo_url(url)
        self._store.record_ingest(
            canonical,
            sha=sha,
            at=at or datetime.now(timezone.utc),
        )

    # ---- status --------------------------------------------------------

    def get_status(self) -> list[RepoStatus]:
        out: list[RepoStatus] = []
        for record in self._store.list_repos():
            clone_path = Path(record.clone_path)
            exists = clone_path.exists()
            size = _dir_size_bytes(clone_path) if exists else 0
            needs_ingest = (
                not record.last_ingested_sha
                or record.last_ingested_sha != record.last_commit_sha
            )
            out.append(
                RepoStatus(
                    record=record,
                    clone_exists=exists,
                    clone_size_bytes=size,
                    needs_ingest=needs_ingest,
                )
            )
        return out


# ---- url helpers -----------------------------------------------------------


_SHORT_FORM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalize_repo_url(value: str) -> str:
    """Accept `owner/name`, `https://github.com/owner/name`, or
    `…/name.git` and return the canonical `https://github.com/owner/name`.

    URLs of any other shape (including filesystem paths and
    GitHub-Enterprise hosts) are returned as-is, with a trailing `.git`
    stripped."""
    value = value.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[: -len(".git")]
    if "://" in value or "@" in value:
        return value
    if value.startswith(("/", ".", "~")):
        return value
    if _SHORT_FORM_RE.match(value):
        owner, name = value.split("/", 1)
        return f"https://github.com/{owner}/{name}"
    raise ValueError(f"invalid repo: {value!r} (expected `owner/name` or full URL)")


def parse_owner_name(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"cannot parse owner/name from {url!r}")
    return parts[0], parts[1]


def _dir_size_bytes(path: Path) -> int:
    total = 0
    try:
        for sub in path.rglob("*"):
            if sub.is_file():
                try:
                    total += sub.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total
