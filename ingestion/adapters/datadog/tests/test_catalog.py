"""Tests for Service Catalog ingestion (phase 3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.adapters.base import IngestionContext
from core.types import Service
from ingestion.adapters.datadog import (
    DatadogAdapter,
    DatadogAdapterConfig,
    DatadogClient,
    DatadogStore,
    RawServiceDefinition,
    RawSpan,
)
from ingestion.adapters.datadog.fetcher import DatadogFetcher
from ingestion.adapters.datadog.parser import (
    DatadogParser,
    merge_catalog_into_services,
)
from ingestion.adapters.datadog.trace_parser import TraceParser

NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _def(
    *,
    name: str,
    team: str = "",
    tier: str = "",
    language: str = "",
    repo_url: str = "",
    owner_email: str = "",
    description: str = "",
    links: dict[str, str] | None = None,
) -> RawServiceDefinition:
    return RawServiceDefinition(
        service_name=name,
        team=team,
        tier=tier,
        languages=(language,) if language else (),
        repos=({"name": "main", "provider": "github", "url": repo_url},) if repo_url else (),
        owner_email=owner_email,
        description=description,
        links=links or {},
    )


def _span(*, service: str = "auth", peer: str | None = "payment") -> RawSpan:
    return RawSpan(
        trace_id="t1",
        span_id="s1",
        parent_id=None,
        service=service,
        resource="POST /charges",
        operation="http.request",
        type="http",
        start=NOW,
        duration_ms=10.0,
        error=False,
        tags={"peer.service": peer} if peer else {},
    )


# ---- store round-trip ------------------------------------------------------


def test_insert_and_read_service_definitions() -> None:
    with DatadogStore(":memory:") as store:
        defs_in = [
            _def(
                name="payment",
                team="billing",
                tier="tier-1",
                language="python",
                repo_url="https://github.com/acme/payment",
                owner_email="billing@acme.io",
                description="charges service",
                links={"runbook": "https://wiki/runbook/payment"},
            ),
            _def(name="auth", team="platform"),
        ]
        wrote = store.insert_service_definitions(defs_in, fetched_at=NOW)
        assert wrote == 2
        assert store.service_definition_count() == 2

        out = sorted(store.read_service_definitions(), key=lambda d: d.service_name)
        assert [d.service_name for d in out] == ["auth", "payment"]
        payment = next(d for d in out if d.service_name == "payment")
        assert payment.team == "billing"
        assert payment.tier == "tier-1"
        assert payment.language == "python"
        assert payment.repo_url == "https://github.com/acme/payment"
        assert payment.links == {"runbook": "https://wiki/runbook/payment"}


def test_service_definitions_upsert_on_replay() -> None:
    with DatadogStore(":memory:") as store:
        store.insert_service_definitions([_def(name="payment", team="billing")])
        store.insert_service_definitions([_def(name="payment", team="commerce")])
        assert store.service_definition_count() == 1
        only = next(iter(store.read_service_definitions()))
        assert only.team == "commerce"


def test_filter_by_team_and_tier() -> None:
    with DatadogStore(":memory:") as store:
        store.insert_service_definitions(
            [
                _def(name="a", team="platform", tier="tier-1"),
                _def(name="b", team="platform", tier="tier-2"),
                _def(name="c", team="billing", tier="tier-1"),
            ]
        )
        platform = list(store.read_service_definitions(team="platform"))
        assert {d.service_name for d in platform} == {"a", "b"}
        tier1 = list(store.read_service_definitions(tier="tier-1"))
        assert {d.service_name for d in tier1} == {"a", "c"}


# ---- merge logic -----------------------------------------------------------


def _svc(name: str, **kwargs) -> Service:
    base = dict(
        id=name,
        name=name,
        repoUrl=f"unknown://{name}",
        createdAt=NOW,
        lastUpdatedAt=NOW,
        isActive=True,
    )
    base.update(kwargs)
    return Service(**base)


def test_merge_overlays_catalog_metadata() -> None:
    """Catalog wins on every metadata field; span-derived id/name preserved."""
    store = DatadogStore(":memory:")
    store.insert_service_definitions(
        [
            _def(
                name="payment",
                team="billing",
                tier="tier-1",
                language="python",
                repo_url="https://github.com/acme/payment",
                owner_email="billing@acme.io",
                description="charges service",
                links={"runbook": "https://wiki"},
            )
        ]
    )
    merged = merge_catalog_into_services([_svc("payment")], store, now=NOW)
    assert len(merged) == 1
    p = merged[0]
    assert p.team == "billing"
    assert p.tier == "tier-1"
    assert p.language == "python"
    assert p.owner == "billing@acme.io"
    assert p.repo_url == "https://github.com/acme/payment"
    assert p.description == "charges service"
    assert p.links == {"runbook": "https://wiki"}


def test_merge_keeps_span_service_when_no_catalog_entry() -> None:
    store = DatadogStore(":memory:")
    store.insert_service_definitions([_def(name="payment", team="billing")])
    merged = merge_catalog_into_services([_svc("auth"), _svc("payment")], store, now=NOW)
    auth = next(s for s in merged if s.name == "auth")
    assert auth.team == ""  # untouched
    assert auth.owner == "unknown"


def test_merge_surfaces_catalog_only_services_as_inactive() -> None:
    store = DatadogStore(":memory:")
    store.insert_service_definitions(
        [
            _def(name="payment", team="billing"),
            _def(name="orphan", team="ghost"),  # no spans for this one
        ]
    )
    merged = merge_catalog_into_services([_svc("payment")], store, now=NOW)
    names = {s.name: s for s in merged}
    assert set(names) == {"payment", "orphan"}
    assert names["orphan"].is_active is False
    assert names["orphan"].team == "ghost"


def test_merge_partial_catalog_only_updates_set_fields() -> None:
    """Empty catalog fields don't override existing service data."""
    store = DatadogStore(":memory:")
    store.insert_service_definitions([_def(name="payment", team="billing")])  # no language
    merged = merge_catalog_into_services([_svc("payment", language="go")], store, now=NOW)
    p = merged[0]
    assert p.team == "billing"  # catalog provides team
    assert p.language == "go"  # span-derived language preserved (catalog empty)


# ---- fetcher --------------------------------------------------------------


class _StubClient(DatadogClient):
    def __init__(self, defs: list[RawServiceDefinition] | None = None) -> None:
        super().__init__(api_key="k", app_key="a")
        self._defs = defs or []
        self.catalog_calls = 0

    def list_service_definitions(self, *, schema_version="v2.2"):  # type: ignore[override]
        self.catalog_calls += 1
        yield from self._defs


def test_fetcher_writes_catalog_and_audits() -> None:
    store = DatadogStore(":memory:")
    client = _StubClient([_def(name="auth"), _def(name="payment")])
    fetcher = DatadogFetcher(store, client)
    count = fetcher.fetch_catalog()
    assert count == 2
    assert store.service_definition_count() == 2
    rec = store.last_fetch("catalog")
    assert rec is not None
    assert rec.status == "success"


def test_fetcher_catalog_failure_audited_then_reraises() -> None:
    class _Boom(DatadogClient):
        def __init__(self) -> None:
            super().__init__(api_key="k", app_key="a")

        def list_service_definitions(self, *, schema_version="v2.2"):  # type: ignore[override]
            raise RuntimeError("api down")
            yield  # pragma: no cover

    store = DatadogStore(":memory:")
    fetcher = DatadogFetcher(store, _Boom())
    from core.types.errors import IngestionError

    with pytest.raises(IngestionError):
        fetcher.fetch_catalog()
    assert store.service_definition_count() == 0
    failures = store.fetch_history(api="catalog")
    assert failures and failures[0].status == "failed"


# ---- parser + adapter integration -----------------------------------------


def test_parser_overlays_catalog_onto_span_services() -> None:
    store = DatadogStore(":memory:")
    store.insert_spans([_span()])
    store.insert_service_definitions(
        [_def(name="auth", team="platform"), _def(name="payment", team="billing")]
    )
    parser = DatadogParser(store, TraceParser(lookback_hours=1, min_span_count=1))
    result = parser.parse(now=NOW)
    by_name = {s.name: s for s in result.services}
    assert by_name["auth"].team == "platform"
    assert by_name["payment"].team == "billing"


def test_adapter_fetches_catalog_when_stale() -> None:
    class _Client(DatadogClient):
        def __init__(self) -> None:
            super().__init__(api_key="k", app_key="a")
            self.span_calls = 0
            self.catalog_calls = 0

        def list_spans(self, *, lookback_hours, query, page_limit=1000, max_pages=100):  # type: ignore[override]
            self.span_calls += 1
            yield _span()

        def list_service_definitions(self, *, schema_version="v2.2"):  # type: ignore[override]
            self.catalog_calls += 1
            yield _def(name="auth", team="platform")
            yield _def(name="payment", team="billing")

    store = DatadogStore(":memory:")
    client = _Client()
    cfg = DatadogAdapterConfig(
        api_key="k", app_key="a",
        lookback_hours=1, min_span_count=1,
        spans_ttl_seconds=300, catalog_ttl_seconds=3600,
    )
    adapter = DatadogAdapter(cfg, client=client, store=store)
    result = adapter.extract(IngestionContext(now=NOW))

    assert client.span_calls == 1
    assert client.catalog_calls == 1
    by_name = {s.name: s for s in result.services}
    assert by_name["auth"].team == "platform"
    assert by_name["payment"].team == "billing"


def test_adapter_skips_catalog_when_fresh() -> None:
    class _Client(DatadogClient):
        def __init__(self) -> None:
            super().__init__(api_key="k", app_key="a")
            self.span_calls = 0
            self.catalog_calls = 0

        def list_spans(self, *, lookback_hours, query, page_limit=1000, max_pages=100):  # type: ignore[override]
            self.span_calls += 1
            yield _span()

        def list_service_definitions(self, *, schema_version="v2.2"):  # type: ignore[override]
            self.catalog_calls += 1
            yield _def(name="auth")

    store = DatadogStore(":memory:")
    # Pre-warm the catalog so its TTL is fresh.
    store.insert_service_definitions([_def(name="auth", team="platform")], fetched_at=NOW)
    store.record_fetch(api="catalog", rows_written=1, fetched_at=NOW)
    client = _Client()
    cfg = DatadogAdapterConfig(
        api_key="k", app_key="a",
        lookback_hours=1, min_span_count=1,
        spans_ttl_seconds=300, catalog_ttl_seconds=3600,
    )
    adapter = DatadogAdapter(cfg, client=client, store=store)
    adapter.extract(IngestionContext(now=NOW))

    assert client.span_calls == 1
    assert client.catalog_calls == 0  # catalog TTL still fresh
