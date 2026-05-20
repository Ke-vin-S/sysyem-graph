"""GitHub adapter configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.config import GitHubSettings


@dataclass
class GitHubAdapterConfig:
    """Bound to the clone-based GitHub ingestor.

    `token` is optional — public repos clone without auth. When set we
    rewrite the clone URL to `https://x-access-token:<token>@github.com/…`
    in `RepoCloner` (and scrub it back after the operation so the token
    never lands in the on-disk `.git/config`)."""

    clones_dir: Path
    store_path: str
    token: str = ""
    default_branch: str = ""
    repos: tuple[str, ...] = ()
    """Optional seed list (from `GITHUB_REPOS`). Repos added via
    `sg-ingest github add` are tracked in the store, NOT here — `repos`
    is only used as a one-time bootstrap for the legacy env-driven
    workflow."""

    @classmethod
    def from_settings(cls, settings: GitHubSettings) -> GitHubAdapterConfig:
        token = settings.token.get_secret_value() if settings.token is not None else ""
        return cls(
            clones_dir=settings.clones_dir,
            store_path=settings.store_path,
            token=token,
            default_branch=settings.default_branch,
            repos=settings.repos,
        )
