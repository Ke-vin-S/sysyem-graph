"""Thin wrapper around datadog-api-client for span ingestion.

We use the v2 SpansApi `list_spans` endpoint which paginates server-side.
The wrapper yields a normalized `RawSpan` so the trace parser doesn't have to
know about Datadog's response envelope shape.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from core.types.errors import IngestionError

logger = logging.getLogger(__name__)

#: Datadog span.type values that mean "this call hits an infrastructure system
#: (database / cache / queue), not another tracked service." The target_name we
#: extract from the tags identifies a host/topic/db, not a peer service — so
#: ExternalConnection.target_service_id stays None.
_INFRA_SPAN_TYPES = frozenset(
    {
        "db", "sql", "postgres", "mysql", "mongodb", "elasticsearch", "cassandra",
        "cache", "redis", "memcached",
        "queue", "kafka", "rabbitmq", "amqp", "sqs", "sns",
    }
)


@dataclass
class RawSpan:
    """Normalized view of one Datadog APM span."""

    trace_id: str
    span_id: str
    parent_id: str | None
    service: str
    """Source service that emitted the span."""

    resource: str
    """For HTTP: 'POST /charges'. For DB: 'SELECT users'. Datadog vocabulary."""

    operation: str
    """e.g. 'http.request', 'grpc.client.call', 'postgres.query'."""

    type: str
    """'http', 'db', 'cache', 'queue', etc."""

    start: datetime
    duration_ms: float
    error: bool = False
    tags: dict[str, str] = field(default_factory=dict)

    def resolve_target(self) -> tuple[str | None, bool]:
        """Return `(target_name, is_service)`.

        `is_service=True` means the callee is another tracked service and we
        should set `ExternalConnection.target_service_id`. `False` means
        the target is infrastructure (DB host, queue topic, cache cluster);
        we record `target_name` only.

        Resolution order:
          1. `peer.service` — Datadog's canonical service tag → service target.
          2. If `span.type` indicates infra, fall through to infra-shaped tags
             (db.instance, kafka.topic, etc.) → infra target.
          3. HTTP/gRPC fallback: `out.host` / `http.host` → service target.
        """
        peer = self.tags.get("peer.service")
        if peer:
            return peer, True
        if self.type in _INFRA_SPAN_TYPES:
            for key in (
                "db.instance",
                "db.name",
                "messaging.destination",
                "kafka.topic",
                "topic",
                "cache.host",
                "out.host",
                "peer.hostname",
            ):
                value = self.tags.get(key)
                if value:
                    return value, False
            return None, False
        for key in ("out.host", "http.host"):
            value = self.tags.get(key)
            if value:
                return value, True
        return None, False

    @property
    def peer_service(self) -> str | None:
        """Backwards-compatible: just the target name when present.
        Prefer `resolve_target()` in new code — it also tells you whether
        the target is a service vs. infrastructure."""
        target, _ = self.resolve_target()
        return target


class DatadogClient:
    """Thin client. Swap out by passing a custom `_api_factory` in tests."""

    def __init__(
        self,
        api_key: str,
        app_key: str,
        site: str = "datadoghq.com",
        *,
        _api_factory: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._app_key = app_key
        self._site = site
        self._api_factory = _api_factory

    def _build_api(self):  # type: ignore[no-untyped-def]
        if self._api_factory is not None:
            return self._api_factory()
        # Imported lazily so test environments without the SDK installed
        # can still import this module.
        from datadog_api_client import ApiClient, Configuration
        from datadog_api_client.v2.api.spans_api import SpansApi

        configuration = Configuration()
        configuration.api_key["apiKeyAuth"] = self._api_key
        configuration.api_key["appKeyAuth"] = self._app_key
        configuration.server_variables["site"] = self._site
        return SpansApi(ApiClient(configuration))

    def list_spans(
        self,
        *,
        lookback_hours: int,
        query: str = "*",
        page_limit: int = 1000,
        max_pages: int = 100,
    ) -> Iterator[RawSpan]:
        """Yield spans from the last `lookback_hours`.

        `max_pages` caps the total number of pages we'll walk so a runaway
        query doesn't hold the run open forever.
        """
        from datadog_api_client.v2.model.spans_list_request import SpansListRequest
        from datadog_api_client.v2.model.spans_list_request_attributes import (
            SpansListRequestAttributes,
        )
        from datadog_api_client.v2.model.spans_list_request_data import SpansListRequestData
        from datadog_api_client.v2.model.spans_list_request_page import SpansListRequestPage
        from datadog_api_client.v2.model.spans_list_request_type import SpansListRequestType
        from datadog_api_client.v2.model.spans_sort import SpansSort

        api = self._build_api()
        now = datetime.now(timezone.utc)
        body = SpansListRequest(
            data=SpansListRequestData(
                attributes=SpansListRequestAttributes(
                    filter={  # type: ignore[arg-type]
                        "from": (now - timedelta(hours=lookback_hours)).isoformat(),
                        "to": now.isoformat(),
                        "query": query,
                    },
                    page=SpansListRequestPage(limit=page_limit),
                    sort=SpansSort.TIMESTAMP_ASCENDING,
                ),
                type=SpansListRequestType.SEARCH_REQUEST,
            )
        )

        pages_seen = 0
        try:
            while pages_seen < max_pages:
                response = api.list_spans(body=body)
                for item in getattr(response, "data", []) or []:
                    parsed = _to_raw_span(item)
                    if parsed is not None:
                        yield parsed
                pages_seen += 1
                cursor = _next_cursor(response)
                if not cursor:
                    return
                body.data.attributes.page = SpansListRequestPage(limit=page_limit, cursor=cursor)
        except Exception as exc:  # pragma: no cover - network/SDK errors
            raise IngestionError("datadog", "list_spans failed", cause=exc) from exc


def _next_cursor(response: Any) -> str | None:
    meta = getattr(response, "meta", None)
    if not meta:
        return None
    page = getattr(meta, "page", None)
    if not page:
        return None
    return getattr(page, "after", None)


def _to_raw_span(item: Any) -> RawSpan | None:
    attrs = getattr(item, "attributes", None)
    if attrs is None:
        return None
    custom = getattr(attrs, "custom", None) or {}
    tags_list = getattr(attrs, "tags", None) or []
    tags = _tags_to_dict(tags_list)
    if isinstance(custom, dict):
        for k, v in custom.items():
            if isinstance(v, str):
                tags.setdefault(k, v)

    start = getattr(attrs, "start_timestamp", None)
    if isinstance(start, datetime):
        start_dt = start
    elif isinstance(start, str):
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    else:
        start_dt = datetime.now(timezone.utc)

    return RawSpan(
        trace_id=str(getattr(attrs, "trace_id", "") or ""),
        span_id=str(getattr(attrs, "span_id", "") or ""),
        parent_id=getattr(attrs, "parent_id", None),
        service=str(getattr(attrs, "service", "") or "unknown"),
        resource=str(getattr(attrs, "resource_name", "") or ""),
        operation=str(getattr(attrs, "name", "") or ""),
        type=str(getattr(attrs, "type", "") or ""),
        start=start_dt,
        duration_ms=float(getattr(attrs, "duration", 0) or 0) / 1_000_000,
        error=bool(getattr(attrs, "error", 0)),
        tags=tags,
    )


def _tags_to_dict(tags: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in tags:
        if not isinstance(tag, str) or ":" not in tag:
            continue
        key, _, value = tag.partition(":")
        out[key.strip()] = value.strip()
    return out
