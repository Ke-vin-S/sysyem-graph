"""GitHubAdapter: clone-based ingestion with SHA-keyed incremental skip.

Loop is:
  1. Read the list of registered repos from the store (or `config.repos`
     as a one-time seed if the store is empty).
  2. For each repo:
       a. `service.ensure_fresh(url)` — clone-or-update, returns SHA.
       b. If `service.is_ingested(url, sha)` AND the clone wasn't a fresh
          one this run → skip.
       c. Else walk the clone with `RepoFetcher.to_records_from_path`
          and append the records.
       d. On success, `service.record_ingest(url, sha)`. (Crashes here
          leave the row pointing at the previous ingested SHA, so the
          next run retries.)
"""

from __future__ import annotations

import logging

from core.adapters.base import AdapterResult, Coverage, IngestionAdapter, IngestionContext
from core.types.errors import IngestionError
from ingestion.adapters.github.auth import AuthError
from ingestion.adapters.github.cloner import RepoCloner
from ingestion.adapters.github.config import GitHubAdapterConfig
from ingestion.adapters.github.repo_fetcher import RepoFetcher
from ingestion.adapters.github.service import GitHubService, normalize_repo_url
from ingestion.adapters.github.store import GitHubStore

logger = logging.getLogger(__name__)


class GitHubAdapter(IngestionAdapter):
    """Walks registered GitHub repos and produces Service + CodeArtifact records.

    Priority 80 (below Datadog's 100). Datadog tells us *what is actually
    called*; GitHub tells us *what exists in source*. When the two disagree
    on connection details, Datadog wins — but only GitHub can produce the
    CodeArtifact-level data the downstream impact rules need.
    """

    name = "github"
    priority = 80

    def __init__(
        self,
        config: GitHubAdapterConfig,
        *,
        service: GitHubService | None = None,
        fetcher: RepoFetcher | None = None,
    ) -> None:
        self._config = config
        if service is None:
            store = GitHubStore(config.store_path)
            cloner = RepoCloner(clones_dir=config.clones_dir)
            service = GitHubService(store=store, cloner=cloner)
        self._service = service
        self._fetcher = fetcher or RepoFetcher()
        # One-time bootstrap: if the user has `GITHUB_REPOS` set and the
        # store doesn't know about them yet, auto-register so the legacy
        # env-driven workflow continues to work.
        if config.repos:
            self._bootstrap_seed_repos()

    def extract(self, context: IngestionContext) -> AdapterResult:
        result = AdapterResult(adapter=self.name)
        registered = self._service.list_repos()
        if not registered:
            result.coverage = Coverage(
                services_scanned=0,
                services_total=0,
                notes="no repos registered — run `sg-ingest github add`",
            )
            return result

        urls = [r.url for r in registered]
        if context.repos:
            allowed = {normalize_repo_url(r) for r in context.repos}
            urls = [u for u in urls if u in allowed]

        total = len(urls)
        scanned = 0
        skipped: list[str] = []

        for url in urls:
            try:
                fresh = self._service.ensure_fresh(url, now=context.now)
            except AuthError as exc:
                # Auth-flavored failures are user-actionable. Surface the
                # doctor message verbatim and keep ingesting the rest.
                logger.warning("github: %s — %s", url, exc.hint)
                result.warnings.append(f"{url}: {exc.hint}")
                continue
            except IngestionError as exc:
                result.warnings.append(f"{url}: {exc}")
                continue
            except Exception as exc:
                raise IngestionError("github", f"unexpected error for {url}", cause=exc) from exc

            if not fresh.was_stale and self._service.is_ingested(url, fresh.sha):
                logger.info("github: %s — cached (sha %s unchanged), skipping", url, fresh.sha[:8])
                skipped.append(url)
                continue

            try:
                service_record, artifacts = self._fetcher.to_records_from_path(
                    fresh.path, repo_id=_repo_id(url), repo_url=url
                )
            except Exception as exc:
                raise IngestionError(
                    "github", f"failed to walk clone for {url}", cause=exc
                ) from exc

            result.services.append(service_record)
            result.artifacts.extend(artifacts)
            self._service.record_ingest(url, sha=fresh.sha, at=context.now)
            scanned += 1

        notes = f"clones_dir={self._config.clones_dir}"
        if skipped:
            notes += f"; skipped={len(skipped)} (sha unchanged)"
        result.coverage = Coverage(
            services_scanned=scanned,
            services_total=total,
            notes=notes,
        )
        return result

    def _bootstrap_seed_repos(self) -> None:
        existing = {r.url for r in self._service.list_repos()}
        for raw in self._config.repos:
            try:
                canonical = normalize_repo_url(raw)
            except ValueError as exc:
                logger.warning("github: ignoring seed repo %r: %s", raw, exc)
                continue
            if canonical in existing:
                continue
            try:
                self._service.add_repo(canonical, branch=self._config.default_branch)
                logger.info("github: registered seed repo %s", canonical)
            except Exception as exc:
                logger.warning("github: failed to register seed repo %s: %s", canonical, exc)


def _repo_id(url: str) -> str:
    """Service IDs throughout the graph are `owner/name`, not the full URL."""
    from urllib.parse import urlparse

    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) < 2:
        return url
    return f"{parts[0]}/{parts[1]}"
