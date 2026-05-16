"""Tests for the adapter framework: registry, merger, mapper, validator, scorer."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.adapters import (
    AdapterRegistry,
    AdapterResult,
    ArtifactConnectionMapper,
    ConfidenceScorer,
    IngestionAdapter,
    IngestionContext,
    ResultMerger,
    ResultValidator,
)
from core.types import (
    CodeArtifact,
    Direction,
    ExternalConnection,
    LineRange,
    Service,
    TestCase,
    TestType,
)
from core.types.errors import IngestionError

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


def _make_service(sid: str) -> Service:
    return Service(
        id=sid,
        name=sid,
        repoUrl=f"https://example.com/{sid}",
        createdAt=NOW,
        lastUpdatedAt=NOW,
        isActive=True,
    )


class _FakeAdapter(IngestionAdapter):
    def __init__(self, name: str, priority: int, services: list[Service]) -> None:
        self.name = name
        self.priority = priority
        self._services = services

    def extract(self, context: IngestionContext) -> AdapterResult:
        result = AdapterResult(adapter=self.name)
        result.services = list(self._services)
        return result


class _ExplodingAdapter(IngestionAdapter):
    name = "exploding"
    priority = 1

    def extract(self, context: IngestionContext) -> AdapterResult:
        raise IngestionError("exploding", "kaboom")


def test_registry_runs_in_priority_order() -> None:
    registry = AdapterRegistry()
    registry.register(_FakeAdapter("low", priority=1, services=[_make_service("a")]))
    registry.register(_FakeAdapter("high", priority=10, services=[_make_service("a")]))
    report = registry.run_all()
    assert [r.adapter for r in report.results] == ["high", "low"]
    # 'high' got there first, so 'low' must show up as a conflict (same id).
    assert any("kept higher-priority" in c for c in report.merged.conflicts)


def test_registry_isolates_failure() -> None:
    registry = AdapterRegistry()
    registry.register(_ExplodingAdapter())
    registry.register(_FakeAdapter("ok", priority=5, services=[_make_service("a")]))
    report = registry.run_all()
    assert "exploding" in report.failures
    assert "ok" in [r.adapter for r in report.results]


def test_registry_unique_registration() -> None:
    registry = AdapterRegistry()
    registry.register(_FakeAdapter("x", priority=1, services=[]))
    with pytest.raises(ValueError):
        registry.register(_FakeAdapter("x", priority=2, services=[]))


def test_result_merger_dedupes_by_id() -> None:
    a = AdapterResult(adapter="a", services=[_make_service("s1")])
    b = AdapterResult(adapter="b", services=[_make_service("s1"), _make_service("s2")])
    merged = ResultMerger().merge([a, b])
    assert set(merged.services) == {"s1", "s2"}
    # a comes first so b's s1 is the conflict.
    assert any("b skipped" in c for c in merged.conflicts)


def test_validator_flags_dangling_connection_source() -> None:
    merger = ResultMerger()
    conn = ExternalConnection(
        id="c1",
        type="http",
        sourceServiceId="ghost",  # not in services
        targetServiceId="s1",
        targetName="s1",
        endpoint="GET /x",
        direction=Direction.OUTBOUND,
        discoveredAt=NOW,
        lastObservedAt=NOW,
    )
    merged = merger.merge(
        [AdapterResult(adapter="a", services=[_make_service("s1")], connections=[conn])]
    )
    report = ResultValidator().validate(merged)
    assert any("unknown source service" in e for e in report.errors)
    assert not report.ok


def test_validator_external_target_is_warning_not_error() -> None:
    merger = ResultMerger()
    conn = ExternalConnection(
        id="c1",
        type="http",
        sourceServiceId="s1",
        targetServiceId="stripe",  # external resource — only warning
        targetName="stripe",
        endpoint="POST /v1/charges",
        direction=Direction.OUTBOUND,
        discoveredAt=NOW,
        lastObservedAt=NOW,
    )
    merged = merger.merge(
        [AdapterResult(adapter="a", services=[_make_service("s1")], connections=[conn])]
    )
    report = ResultValidator().validate(merged)
    assert report.ok
    assert any("external resource" in w for w in report.warnings)


def test_mapper_links_endpoint_artifact_to_traced_connection() -> None:
    artifact = CodeArtifact(
        id="endpoint:payment:POST:/users/{id}/charges",
        repoId="payment",
        type="endpoint",
        name="POST /users/{id}/charges",
        file="src/handlers.py",
        lineRange=LineRange(start=10, end=10),
        isPublic=True,
    )
    conn = ExternalConnection(
        id="c1",
        type="http",
        sourceServiceId="auth",
        targetServiceId="payment",
        targetName="payment",
        endpoint="POST /users/42/charges",
        direction=Direction.OUTBOUND,
        discoveredAt=NOW,
        lastObservedAt=NOW,
    )
    out = ArtifactConnectionMapper().map([artifact], [conn])
    assert out[0].external_connections == ("c1",)


def test_mapper_leaves_non_endpoints_alone() -> None:
    artifact = CodeArtifact(
        id="fn:payment:src/utils.py:helper",
        repoId="payment",
        type="function",
        name="helper",
        file="src/utils.py",
        lineRange=LineRange(start=1, end=1),
    )
    out = ArtifactConnectionMapper().map([artifact], [])
    assert out[0] is artifact


def test_confidence_scorer_trusts_datadog_more_than_github() -> None:
    scorer = ConfidenceScorer()
    conn = ExternalConnection(
        id="c1",
        type="http",
        sourceServiceId="a",
        targetServiceId="b",
        targetName="b",
        endpoint="POST /x",
        direction=Direction.OUTBOUND,
        frequency=50,
        discoveredAt=NOW,
        lastObservedAt=NOW,
    )
    dd = scorer.score(conn, source_adapter="datadog")
    gh = scorer.score(conn, source_adapter="github")
    assert dd > gh
    assert 0 < dd <= 1.0


def test_confidence_scorer_frequency_boost_is_capped() -> None:
    scorer = ConfidenceScorer()
    conn = ExternalConnection(
        id="c1",
        type="http",
        sourceServiceId="a",
        targetServiceId="b",
        targetName="b",
        endpoint="POST /x",
        direction=Direction.OUTBOUND,
        frequency=10_000,
        discoveredAt=NOW,
        lastObservedAt=NOW,
    )
    assert scorer.score(conn, source_adapter="datadog") <= 1.0


def test_test_case_serializes() -> None:
    # smoke test that TestCase is JSON-round-trippable for the CLI dump.
    tc = TestCase(
        id="t1",
        repoId="auth",
        type=TestType.INTEGRATION,
        name="test_thing",
        file="tests/test_thing.py",
        lineRange=LineRange(start=1, end=5),
    )
    data = tc.model_dump(mode="json")
    assert data["type"] == "INTEGRATION"
    assert TestCase.model_validate(data) == tc
