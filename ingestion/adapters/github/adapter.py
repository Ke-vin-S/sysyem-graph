"""GitHubAdapter: fetch repos and emit Service + CodeArtifact records."""

from __future__ import annotations

import logging

from core.adapters.base import AdapterResult, Coverage, IngestionAdapter, IngestionContext
from core.types.errors import IngestionError
from ingestion.adapters.github.client import GitHubClient
from ingestion.adapters.github.config import GitHubAdapterConfig
from ingestion.adapters.github.repo_fetcher import RepoFetcher

logger = logging.getLogger(__name__)


class GitHubAdapter(IngestionAdapter):
    """Walks a list of GitHub repos and produces Service + CodeArtifact records.

    Priority 80 (below Datadog's 100). Datadog tells us *what is actually
    called* in production; GitHub tells us *what exists in source*. When the
    two disagree on connection details, Datadog wins — but only GitHub can
    produce the CodeArtifact-level data the downstream impact rules need.
    """

    name = "github"
    priority = 80

    def __init__(
        self,
        config: GitHubAdapterConfig,
        *,
        client: GitHubClient | None = None,
        fetcher: RepoFetcher | None = None,
    ) -> None:
        if not config.repos:
            raise ValueError("GitHubAdapter requires at least one repo in config.repos")
        self._config = config
        self._client = client or GitHubClient(token=config.token, api_url=config.api_url)
        self._fetcher = fetcher or RepoFetcher()

    def extract(self, context: IngestionContext) -> AdapterResult:
        result = AdapterResult(adapter=self.name)
        repos = context.repos or self._config.repos
        total = len(repos)
        scanned = 0

        for full_name in repos:
            logger.info("github: fetching %s", full_name)
            try:
                snapshot = self._client.fetch_repo(
                    full_name,
                    file_extensions=self._config.file_extensions,
                    max_file_bytes=self._config.max_file_bytes,
                )
            except IngestionError as exc:
                result.warnings.append(f"{full_name}: {exc}")
                continue
            except Exception as exc:
                raise IngestionError("github", f"unexpected error for {full_name}", cause=exc) from exc

            service, artifacts = self._fetcher.to_records(snapshot)
            result.services.append(service)
            result.artifacts.extend(artifacts)
            scanned += 1

        result.coverage = Coverage(
            services_scanned=scanned,
            services_total=total,
            notes=f"file_extensions={self._config.file_extensions}",
        )
        return result
