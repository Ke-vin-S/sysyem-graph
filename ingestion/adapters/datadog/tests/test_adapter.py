"""Unit tests for the Datadog adapter and its trace parser."""

from __future__ import annotations

from datetime import datetime, timezone

from core.adapters import IngestionContext
from core.types import Criticality
from ingestion.adapters.datadog import (
    DatadogAdapter,
    DatadogAdapterConfig,
    DatadogClient,
    RawSpan,
    TraceParser,
)

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


def _span(
    *,
    service: str,
    peer: str | None,
    resource: str,
    span_type: str = "http",
    error: bool = False,
    start: datetime | None = None,
    extra_tags: dict[str, str] | None = None,
) -> RawSpan:
    tags: dict[str, str] = {}
    if peer:
        tags["peer.service"] = peer
    if extra_tags:
        tags.update(extra_tags)
    return RawSpan(
        trace_id="t1",
        span_id="s1",
        parent_id=None,
        service=service,
        resource=resource,
        operation=f"{span_type}.request",
        type=span_type,
        start=start or NOW,
        duration_ms=10.0,
        error=error,
        tags=tags,
    )


def test_trace_parser_buckets_spans_by_endpoint() -> None:
    parser = TraceParser(lookback_hours=1, min_span_count=1)
    spans = [
        _span(service="auth", peer="payment", resource="POST /charges"),
        _span(service="auth", peer="payment", resource="POST /charges"),
        _span(service="auth", peer="payment", resource="GET /balance"),
    ]
    result = parser.parse(spans, now=NOW)
    endpoints = sorted(c.endpoint for c in result.connections)
    assert endpoints == ["GET /balance", "POST /charges"]
    charges = next(c for c in result.connections if c.endpoint == "POST /charges")
    assert charges.source_service_id == "auth"
    assert charges.target_service_id == "payment"
    assert "spans_observed" in charges.data_flow
    assert charges.data_flow["spans_observed"] == "2"


def test_trace_parser_skips_internal_spans() -> None:
    parser = TraceParser(lookback_hours=1, min_span_count=1)
    spans = [_span(service="auth", peer=None, resource="local")]
    result = parser.parse(spans, now=NOW)
    assert result.connections == []
    # but the service is still recorded as seen.
    assert [s.id for s in result.services] == ["auth"]


def test_trace_parser_min_count_filters_one_offs() -> None:
    parser = TraceParser(lookback_hours=1, min_span_count=5)
    spans = [_span(service="auth", peer="payment", resource="POST /charges")]
    result = parser.parse(spans, now=NOW)
    assert result.connections == []


def test_trace_parser_criticality_from_frequency() -> None:
    parser = TraceParser(lookback_hours=1, min_span_count=1)
    # 120 spans inside a 1-hour window = 2/min. Below the HIGH threshold (>=10/min).
    # Need >=600 spans to hit >=10/min.
    spans = [_span(service="auth", peer="payment", resource="POST /charges") for _ in range(700)]
    result = parser.parse(spans, now=NOW)
    assert result.connections[0].criticality in {Criticality.HIGH, Criticality.CRITICAL}


def test_adapter_invokes_client_and_parser() -> None:
    config = DatadogAdapterConfig(api_key="k", app_key="a")

    class _StubClient(DatadogClient):
        def __init__(self) -> None:
            super().__init__(api_key="k", app_key="a")

        def list_spans(self, *, lookback_hours, query, page_limit=1000, max_pages=100):  # type: ignore[override]
            yield _span(service="auth", peer="payment", resource="POST /charges")
            yield _span(service="auth", peer="payment", resource="POST /charges")

    adapter = DatadogAdapter(config, client=_StubClient())
    result = adapter.extract(IngestionContext(now=NOW))
    assert result.adapter == "datadog"
    assert {s.id for s in result.services} == {"auth", "payment"}
    assert len(result.connections) == 1
    assert result.connections[0].endpoint == "POST /charges"


def test_adapter_query_includes_allowlist() -> None:
    config = DatadogAdapterConfig(api_key="k", app_key="a", services_allowlist=("auth", "payment"))
    adapter = DatadogAdapter(config, client=DatadogClient(api_key="k", app_key="a"))
    query = adapter._build_query(IngestionContext())  # noqa: SLF001
    assert "service:auth" in query
    assert " OR " in query


def test_adapter_query_includes_env_filter() -> None:
    config = DatadogAdapterConfig(api_key="k", app_key="a", env="prod")
    adapter = DatadogAdapter(config, client=DatadogClient(api_key="k", app_key="a"))
    query = adapter._build_query(IngestionContext())  # noqa: SLF001
    assert query == "env:prod"


def test_adapter_query_combines_env_and_allowlist() -> None:
    config = DatadogAdapterConfig(
        api_key="k", app_key="a", env="prod", services_allowlist=("auth", "payment")
    )
    adapter = DatadogAdapter(config, client=DatadogClient(api_key="k", app_key="a"))
    query = adapter._build_query(IngestionContext())  # noqa: SLF001
    assert query.startswith("env:prod AND ")
    assert "service:auth" in query and "service:payment" in query
    assert " OR " in query  # services joined with OR inside their group


def test_adapter_query_env_only_with_single_service() -> None:
    config = DatadogAdapterConfig(
        api_key="k", app_key="a", env="staging", services_allowlist=("auth",)
    )
    adapter = DatadogAdapter(config, client=DatadogClient(api_key="k", app_key="a"))
    query = adapter._build_query(IngestionContext())  # noqa: SLF001
    assert query == "env:staging AND service:auth"


# ---- infra targets (db/cache/queue) ---------------------------------------


def test_trace_parser_emits_db_connection_with_null_service_id() -> None:
    parser = TraceParser(lookback_hours=1, min_span_count=1)
    spans = [
        _span(
            service="auth",
            peer=None,
            resource="SELECT users",
            span_type="postgres",
            extra_tags={"db.instance": "users-primary"},
        )
    ]
    result = parser.parse(spans, now=NOW)
    assert len(result.connections) == 1
    conn = result.connections[0]
    assert conn.target_service_id is None
    assert conn.target_name == "users-primary"
    assert conn.endpoint == "SELECT users"
    # The DB host must NOT show up as a Service node.
    assert {s.id for s in result.services} == {"auth"}


def test_trace_parser_emits_kafka_connection_from_topic_tag() -> None:
    parser = TraceParser(lookback_hours=1, min_span_count=1)
    spans = [
        _span(
            service="auth",
            peer=None,
            resource="produce orders",
            span_type="kafka",
            extra_tags={"messaging.destination": "orders.v1"},
        )
    ]
    result = parser.parse(spans, now=NOW)
    assert len(result.connections) == 1
    conn = result.connections[0]
    assert conn.target_service_id is None
    assert conn.target_name == "orders.v1"
    assert conn.type == "kafka"


def test_trace_parser_emits_redis_connection() -> None:
    parser = TraceParser(lookback_hours=1, min_span_count=1)
    spans = [
        _span(
            service="auth",
            peer=None,
            resource="GET session:42",
            span_type="redis",
            extra_tags={"out.host": "redis-cache.internal"},
        )
    ]
    result = parser.parse(spans, now=NOW)
    assert len(result.connections) == 1
    assert result.connections[0].target_name == "redis-cache.internal"
    assert result.connections[0].target_service_id is None
    # Redis target is NOT added as a service.
    assert {s.id for s in result.services} == {"auth"}


def test_trace_parser_service_target_still_sets_target_service_id() -> None:
    # Regression: the new infra path must not break ordinary service-to-service edges.
    parser = TraceParser(lookback_hours=1, min_span_count=1)
    spans = [_span(service="auth", peer="payment", resource="POST /charges")]
    result = parser.parse(spans, now=NOW)
    assert len(result.connections) == 1
    assert result.connections[0].target_service_id == "payment"
    assert result.connections[0].target_name == "payment"


def test_trace_parser_infra_span_without_any_target_tag_is_skipped() -> None:
    parser = TraceParser(lookback_hours=1, min_span_count=1)
    spans = [
        _span(
            service="auth",
            peer=None,
            resource="SELECT 1",
            span_type="postgres",
            extra_tags={},
        )
    ]
    result = parser.parse(spans, now=NOW)
    assert result.connections == []
    assert result.spans_skipped == 1
