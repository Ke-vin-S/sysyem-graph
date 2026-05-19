"""DatadogAdapter: extracts inter-service connections from APM traces."""

from __future__ import annotations

import logging

from core.adapters.base import AdapterResult, Coverage, IngestionAdapter, IngestionContext
from core.types.errors import IngestionError
from ingestion.adapters.datadog.client import DatadogClient
from ingestion.adapters.datadog.config import DatadogAdapterConfig
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
    ) -> None:
        self._config = config
        self._client = client or DatadogClient(
            api_key=config.api_key,
            app_key=config.app_key,
            site=config.site,
        )
        self._parser = parser or TraceParser(
            lookback_hours=config.lookback_hours,
            min_span_count=config.min_span_count,
        )

    def extract(self, context: IngestionContext) -> AdapterResult:
        query = self._build_query(context)
        logger.info(
            "datadog: querying spans (lookback=%dh, query=%r)",
            self._config.lookback_hours,
            query,
        )
        try:
            spans = self._client.list_spans(
                lookback_hours=self._config.lookback_hours,
                query=query,
            )
            parsed = self._parser.parse(spans, now=context.now)
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError("datadog", "trace parsing failed", cause=exc) from exc

        result = AdapterResult(adapter=self.name)
        result.services = parsed.services
        result.connections = parsed.connections
        result.coverage = Coverage(
            services_scanned=len(parsed.services),
            services_total=len(parsed.services) or None,
            notes=f"spans seen={parsed.spans_seen}, skipped={parsed.spans_skipped}",
        )
        if parsed.spans_seen == 0:
            result.warnings.append("no spans returned for window")
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
