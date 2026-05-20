"""GitHub ingestion adapter: registered repos -> shallow clones -> Service + CodeArtifacts."""

from ingestion.adapters.github.adapter import GitHubAdapter
from ingestion.adapters.github.cloner import CloneResult, RepoCloner
from ingestion.adapters.github.config import GitHubAdapterConfig
from ingestion.adapters.github.repo_fetcher import RepoFetcher
from ingestion.adapters.github.service import (
    FreshResult,
    GitHubService,
    RepoStatus,
    normalize_repo_url,
    parse_owner_name,
)
from ingestion.adapters.github.store import GitHubStore, RepoRecord

__all__ = [
    "CloneResult",
    "FreshResult",
    "GitHubAdapter",
    "GitHubAdapterConfig",
    "GitHubService",
    "GitHubStore",
    "RepoCloner",
    "RepoFetcher",
    "RepoRecord",
    "RepoStatus",
    "normalize_repo_url",
    "parse_owner_name",
]
