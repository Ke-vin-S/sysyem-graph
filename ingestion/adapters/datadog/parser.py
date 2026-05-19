"""DatadogParser: read spans from DatadogStore, run TraceParser, return result.

Strict separation from the fetcher: parser reads only, never hits Datadog.
Same store, same spans → same output. The parser is the bridge between
storage and the impact-graph data model (Service + ExternalConnection).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.types import Service
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
        result.services = merge_catalog_into_services(result.services, self._store, now=when)
        logger.info(
            "datadog_parser: %d spans -> %d services, %d connections",
            result.spans_seen,
            len(result.services),
            len(result.connections),
        )
        return result


def merge_catalog_into_services(
    services: list[Service],
    store: DatadogStore,
    *,
    now: datetime | None = None,
) -> list[Service]:
    """Enrich span-derived `Service` records with Service Catalog metadata.

    Catalog wins on every metadata field (it's authoritative); span data
    contributes `last_updated_at` (when the service was last observed) and
    is the only signal that promotes a service from "in catalog" to
    "in catalog AND alive in prod."

    Also surfaces catalog-only services that have no traffic yet — they
    show up in the graph but `is_active=False` and `last_updated_at`
    matches the catalog fetch time, so callers can distinguish them.
    """
    when = now or datetime.now(timezone.utc)
    defs = {d.service_name: d for d in store.read_service_definitions()}
    if not defs:
        return services

    by_id = {s.id: s for s in services}
    out: list[Service] = []
    seen_names: set[str] = set()
    for svc in services:
        d = defs.get(svc.name)
        if d is None:
            out.append(svc)
            continue
        seen_names.add(svc.name)
        updates: dict[str, object] = {}
        if d.repo_url:
            updates["repo_url"] = d.repo_url
        if d.language:
            updates["language"] = d.language
        if d.owner_email:
            updates["owner"] = d.owner_email
        if d.team:
            updates["team"] = d.team
        if d.tier:
            updates["tier"] = d.tier
        if d.description:
            updates["description"] = d.description
        if d.links:
            updates["links"] = dict(d.links)
        out.append(svc.model_copy(update=updates) if updates else svc)

    # Catalog-only services (no spans yet) — add as inactive entries so
    # operators can still see them in the inventory.
    for name, d in defs.items():
        if name in seen_names or name in by_id:
            continue
        out.append(
            Service(
                id=name,
                name=name,
                repoUrl=d.repo_url or f"unknown://{name}",
                language=d.language or "unknown",
                framework="unknown",
                owner=d.owner_email or "unknown",
                team=d.team,
                tier=d.tier,
                description=d.description,
                links=dict(d.links),
                createdAt=when,
                lastUpdatedAt=when,
                isActive=False,
            )
        )
    return out
