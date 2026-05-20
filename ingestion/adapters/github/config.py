"""GitHub adapter configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.config import GitHubSettings


@dataclass
class GitHubAdapterConfig:
    """Bound to the clone-based GitHub ingestor.

    Tokens are resolved per clone-host by `TokenResolver` (reading
    `GITHUB_TOKEN` / `GITHUB_TOKEN_<HOST>` from the environment), so this
    config carries no token field — `.env` is the system of record."""

    clones_dir: Path
    store_path: str
    default_branch: str = ""
    repos: tuple[str, ...] = ()
    """Optional seed list (from `GITHUB_REPOS`). Repos added via
    `sg-ingest github add` are tracked in the store, NOT here — `repos`
    is only used as a one-time bootstrap for the legacy env-driven
    workflow."""

    @classmethod
    def from_settings(cls, settings: GitHubSettings) -> GitHubAdapterConfig:
        return cls(
            clones_dir=settings.clones_dir,
            store_path=settings.store_path,
            default_branch=settings.default_branch,
            repos=settings.repos,
        )
