"""DatadogParser: read spans from DatadogStore, run TraceParser, return result.

Strict separation from the fetcher: parser reads only, never hits Datadog.
Same store, same spans → same output. The parser is the bridge between
storage and the impact-graph data model (Service + ExternalConnection).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ingestion.adapters.datadog.store import DatadogStore
from ingestion.adapters.datadog.trace_parser import ParseResult, TraceParser

logger = logging.getLogger(__name__)


class DatadogParser:
    """Drives TraceParser over a sliced view of staged spans."""

    def __init__(
        self,
        store: DatadogStore,
        trace_parser: TraceParser,
    ) -> None:
        self._store = store
        self._trace_parser = trace_parser

    def parse(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        env: str | None = None,
        now: datetime | None = None,
    ) -> ParseResult:
        """Read spans matching the filter, bucket them, return ParseResult.

        Filters compose with AND. Pass `since`/`until` to narrow the
        window (parser-side, no re-fetch); `env` to scope by Datadog
        environment tag. Empty filters = the whole store.
        """
        when = now or datetime.now(timezone.utc)
        spans = self._store.read_spans(since=since, until=until, env=env)
        result = self._trace_parser.parse(spans, now=when)
        logger.info(
            "datadog_parser: %d spans -> %d services, %d connections",
            result.spans_seen,
            len(result.services),
            len(result.connections),
        )
        return result
