"""GitHub ingestion adapter: repos -> Services + CodeArtifacts."""

from ingestion.adapters.github.adapter import GitHubAdapter
from ingestion.adapters.github.client import GitHubClient, RepoSnapshot, RepoFile
from ingestion.adapters.github.config import GitHubAdapterConfig
from ingestion.adapters.github.repo_fetcher import RepoFetcher

__all__ = [
    "GitHubAdapter",
    "GitHubAdapterConfig",
    "GitHubClient",
    "RepoFetcher",
    "RepoFile",
    "RepoSnapshot",
]
