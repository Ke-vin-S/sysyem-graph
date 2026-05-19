"""DatadogAdapter: extracts inter-service connections from APM traces.

Flow (phase 2):
  1. `extract` checks whether the spans table is stale beyond `spans_ttl_seconds`.
  2. If stale, `DatadogFetcher` pulls the lookback window into the store.
  3. `DatadogParser` reads the staged spans and runs `TraceParser`.

The two halves are split so an operator can also run them independently
via `sg-ingest datadog-fetch` and `sg-ingest datadog-parse`, replaying
the parser as often as needed without re-burning Datadog API quota.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from core.adapters.base import AdapterResult, Coverage, IngestionAdapter, IngestionContext
from core.types.errors import IngestionError
from ingestion.adapters.datadog.client import DatadogClient
from ingestion.adapters.datadog.config import DatadogAdapterConfig
from ingestion.adapters.datadog.fetcher import DatadogFetcher
from ingestion.adapters.datadog.parser import DatadogParser
from ingestion.adapters.datadog.store import DatadogStore
from ingestion.adapters.datadog.trace_parser import TraceParser

logger = logging.getLogger(__name__)


class DatadogAdapter(IngestionAdapter):
    """Pulls APM spans from Datadog and emits Services + ExternalConnections.

    Highest-priority adapter (priority=100): traces are ground truth about what
    services actually call each other in production. Code-static-analysis edges
    are inferential and yield to traced ones during the merge step.
    """

    name = "datadog"
    priority = 100

    def __init__(
        self,
        config: DatadogAdapterConfig,
        *,
        client: DatadogClient | None = None,
        parser: TraceParser | None = None,
        store: DatadogStore | None = None,
        fetcher: DatadogFetcher | None = None,
        datadog_parser: DatadogParser | None = None,
    ) -> None:
        self._config = config
        self._client = client or DatadogClient(
            api_key=config.api_key,
            app_key=config.app_key,
            site=config.site,
        )
        # `store=None` defaults to in-memory so unit tests get isolation for
        # free; production constructs an on-disk store from settings.
        self._store = store if store is not None else DatadogStore(":memory:")
        self._fetcher = fetcher or DatadogFetcher(self._store, self._client)
        trace_parser = parser or TraceParser(
            lookback_hours=config.lookback_hours,
            min_span_count=config.min_span_count,
        )
        self._parser = datadog_parser or DatadogParser(self._store, trace_parser)

    @property
    def store(self) -> DatadogStore:
        """Exposed so CLI subcommands can read history / stats."""
        return self._store

    def extract(self, context: IngestionContext) -> AdapterResult:
        # Fetch only if our staged spans are older than the TTL — replays
        # of `run` within the window reuse the cache instead of re-hitting
        # Datadog.
        if self._store.is_stale("spans", ttl_seconds=self._config.spans_ttl_seconds):
            query = self._build_query(context)
            logger.info(
                "datadog: span cache stale, fetching (lookback=%dh, query=%r)",
                self._config.lookback_hours,
                query,
            )
            try:
                self._fetcher.fetch_spans(
                    lookback_hours=self._config.lookback_hours,
                    query=query,
                    env=self._config.env,
                )
            except IngestionError:
                raise
            except Exception as exc:
                raise IngestionError("datadog", "span fetch failed", cause=exc) from exc
        else:
            last = self._store.last_fetched_at("spans")
            logger.info("datadog: span cache fresh (last fetched at %s); skipping fetch", last)

        # Catalog has its own TTL — usually much longer than spans
        # (definitions change rarely). Failures here don't kill the run;
        # we just log and continue with whatever's already staged.
        if self._store.is_stale("catalog", ttl_seconds=self._config.catalog_ttl_seconds):
            logger.info("datadog: catalog cache stale, fetching")
            try:
                self._fetcher.fetch_catalog()
            except IngestionError as exc:
                logger.warning("datadog: catalog fetch failed: %s; continuing", exc)
        else:
            logger.info("datadog: catalog cache fresh; skipping fetch")

        # Parse from the store, scoped to the configured lookback window
        # (the store may hold older spans too). The parser also overlays
        # catalog metadata onto the span-derived services and surfaces
        # any catalog-only services as inactive entries.
        since = context.now - timedelta(hours=self._config.lookback_hours)
        parsed = self._parser.parse(since=since, now=context.now)

        result = AdapterResult(adapter=self.name)
        result.services = parsed.services
        result.connections = parsed.connections
        result.coverage = Coverage(
            services_scanned=len(parsed.services),
            services_total=len(parsed.services) or None,
            notes=f"spans seen={parsed.spans_seen}, skipped={parsed.spans_skipped}",
        )
        if parsed.spans_seen == 0:
            result.warnings.append("no spans in store for window")
        return result

    def _build_query(self, context: IngestionContext) -> str:
        clauses: list[str] = []
        if self._config.env:
            clauses.append(f"env:{self._config.env}")
        allowlist = self._config.services_allowlist or context.repos
        if allowlist:
            services_clause = " OR ".join(f"service:{svc}" for svc in allowlist)
            if len(allowlist) > 1:
                services_clause = f"({services_clause})"
            clauses.append(services_clause)
        if not clauses:
            return "*"
        return " AND ".join(clauses)
