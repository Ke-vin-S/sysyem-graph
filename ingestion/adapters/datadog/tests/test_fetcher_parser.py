"""Tests for the fetch/parse split: DatadogFetcher, DatadogParser, and
the adapter-level fetch-if-stale orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.adapters.base import IngestionContext
from core.types.errors import IngestionError
from ingestion.adapters.datadog import (
    DatadogAdapter,
    DatadogAdapterConfig,
    DatadogClient,
    DatadogStore,
    RawSpan,
    TraceParser,
)
from ingestion.adapters.datadog.fetcher import DatadogFetcher
from ingestion.adapters.datadog.parser import DatadogParser

NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _span(
    *,
    trace_id: str = "t1",
    span_id: str = "s1",
    parent_id: str | None = None,
    service: str = "auth",
    peer: str | None = "payment",
    resource: str = "POST /charges",
    span_type: str = "http",
    start: datetime | None = None,
    error: bool = False,
) -> RawSpan:
    tags = {"peer.service": peer} if peer else {}
    return RawSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_id=parent_id,
        service=service,
        resource=resource,
        operation=f"{span_type}.request",
        type=span_type,
        start=start or NOW,
        duration_ms=10.0,
        error=error,
        tags=tags,
    )


class _StubClient(DatadogClient):
    """Replays a fixed span list. Counts how many times list_spans was called."""

    def __init__(self, spans: list[RawSpan]) -> None:
        super().__init__(api_key="k", app_key="a")
        self._spans = spans
        self.calls = 0

    def list_spans(self, *, lookback_hours, query, page_limit=1000, max_pages=100):  # type: ignore[override]
        self.calls += 1
        yield from self._spans


class _ExplodingClient(DatadogClient):
    def __init__(self) -> None:
        super().__init__(api_key="k", app_key="a")

    def list_spans(self, *, lookback_hours, query, page_limit=1000, max_pages=100):  # type: ignore[override]
        raise RuntimeError("boom")
        yield  # pragma: no cover - make it a generator


# ---- DatadogFetcher --------------------------------------------------------


def test_fetcher_writes_spans_and_audit_row() -> None:
    store = DatadogStore(":memory:")
    client = _StubClient([_span(span_id="a"), _span(span_id="b", trace_id="t2")])
    fetcher = DatadogFetcher(store, client)

    count = fetcher.fetch_spans(lookback_hours=1)

    assert count == 2
    assert store.span_count() == 2
    rec = store.last_fetch("spans")
    assert rec is not None
    assert rec.status == "success"
    assert rec.rows_written == 2


def test_fetcher_records_failure_then_reraises() -> None:
    store = DatadogStore(":memory:")
    fetcher = DatadogFetcher(store, _ExplodingClient())

    with pytest.raises(IngestionError):
        fetcher.fetch_spans(lookback_hours=1)

    # No spans landed.
    assert store.span_count() == 0
    # Failure row is audited so the next run knows to retry.
    failures = store.fetch_history(api="spans")
    assert len(failures) == 1
    assert failures[0].status == "failed"
    assert "boom" in failures[0].error


def test_fetcher_env_recorded_on_spans() -> None:
    store = DatadogStore(":memory:")
    fetcher = DatadogFetcher(store, _StubClient([_span()]))
    fetcher.fetch_spans(lookback_hours=1, env="prod")
    prod = list(store.read_spans(env="prod"))
    assert len(prod) == 1


# ---- DatadogParser ---------------------------------------------------------


def test_parser_buckets_staged_spans() -> None:
    store = DatadogStore(":memory:")
    store.insert_spans(
        [
            _span(trace_id="t1", span_id="a", service="auth", peer="payment"),
            _span(trace_id="t1", span_id="b", service="auth", peer="payment"),
        ]
    )
    parser = DatadogParser(store, TraceParser(lookback_hours=1, min_span_count=1))
    result = parser.parse(now=NOW)

    assert {c.endpoint for c in result.connections} == {"POST /charges"}
    assert result.connections[0].data_flow["spans_observed"] == "2"


def test_parser_window_filter_excludes_old_spans() -> None:
    store = DatadogStore(":memory:")
    store.insert_spans(
        [
            _span(trace_id="t1", span_id="old", start=NOW - timedelta(hours=10)),
            _span(trace_id="t2", span_id="new", start=NOW),
        ]
    )
    parser = DatadogParser(store, TraceParser(lookback_hours=1, min_span_count=1))
    # `since=NOW-1h` drops the old span — parser only sees the recent one.
    result = parser.parse(since=NOW - timedelta(hours=1), now=NOW)
    assert result.spans_seen == 1


def test_parser_env_filter_scopes_to_env() -> None:
    store = DatadogStore(":memory:")
    store.insert_spans([_span(trace_id="t1", span_id="a")], env="prod")
    store.insert_spans([_span(trace_id="t2", span_id="b")], env="staging")
    parser = DatadogParser(store, TraceParser(lookback_hours=1, min_span_count=1))
    result = parser.parse(env="prod", now=NOW)
    assert result.spans_seen == 1


# ---- DatadogAdapter integration -------------------------------------------


def _cfg(**overrides) -> DatadogAdapterConfig:
    base = dict(api_key="k", app_key="a", lookback_hours=1, min_span_count=1)
    base.update(overrides)
    return DatadogAdapterConfig(**base)


def test_adapter_fetches_then_parses_when_cache_is_empty() -> None:
    store = DatadogStore(":memory:")
    client = _StubClient([_span(span_id="a"), _span(span_id="b", trace_id="t2")])
    adapter = DatadogAdapter(_cfg(spans_ttl_seconds=300), client=client, store=store)

    result = adapter.extract(IngestionContext(now=NOW))

    assert client.calls == 1
    assert {s.id for s in result.services} == {"auth", "payment"}
    assert len(result.connections) == 1


def test_adapter_skips_fetch_when_cache_is_fresh() -> None:
    """Within TTL, repeated `extract` must not re-hit Datadog."""
    store = DatadogStore(":memory:")
    client = _StubClient([_span(span_id="a")])
    adapter = DatadogAdapter(_cfg(spans_ttl_seconds=600), client=client, store=store)

    adapter.extract(IngestionContext(now=NOW))
    adapter.extract(IngestionContext(now=NOW))
    adapter.extract(IngestionContext(now=NOW))

    # Only the first call hit the API; the next two read from the store.
    assert client.calls == 1
    assert store.span_count() == 1


def test_adapter_refetches_when_ttl_expires() -> None:
    store = DatadogStore(":memory:")
    client = _StubClient([_span(span_id="a")])
    adapter = DatadogAdapter(_cfg(spans_ttl_seconds=300), client=client, store=store)

    adapter.extract(IngestionContext(now=NOW))
    assert client.calls == 1

    # Backdate the recorded fetch so TTL has expired.
    with store._txn() as cur:  # noqa: SLF001
        cur.execute(
            "UPDATE fetch_log SET fetched_at = ? WHERE api = 'spans'",
            ((NOW - timedelta(hours=1)).isoformat(),),
        )

    adapter.extract(IngestionContext(now=NOW))
    assert client.calls == 2
