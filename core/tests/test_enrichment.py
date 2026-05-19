"""Tests for the post-merge enrichment pass."""

from __future__ import annotations

from datetime import datetime, timezone

from core.adapters.enrichment import link_connections_to_endpoints
from core.adapters.merger import MergedResult
from core.types import (
    ContractStatus,
    Criticality,
    Direction,
    Endpoint,
    ExternalConnection,
)

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


def _conn(
    *,
    cid: str,
    source: str,
    target_service: str | None,
    endpoint: str,
) -> ExternalConnection:
    return ExternalConnection(
        id=cid,
        type="http",
        sourceServiceId=source,
        targetServiceId=target_service,
        targetName=target_service or "external",
        protocol="http",
        endpoint=endpoint,
        direction=Direction.OUTBOUND,
        frequency=1.0,
        criticality=Criticality.MEDIUM,
        contractStatus=ContractStatus.UNKNOWN,
        discoveredAt=NOW,
        lastObservedAt=NOW,
    )


def _ep(*, repo_id: str, method: str, path: str, eid: str | None = None) -> Endpoint:
    return Endpoint(
        id=eid or f"endpoint:{repo_id}:{method}:{path}",
        repoId=repo_id,
        method=method,
        path=path,
        framework="fastapi",
    )


def _merged(*, connections: list[ExternalConnection], endpoints: list[Endpoint]) -> MergedResult:
    m = MergedResult()
    for c in connections:
        m.connections[c.id] = c
    for e in endpoints:
        m.endpoints[e.id] = e
    return m


def test_links_exact_method_path_match() -> None:
    conn = _conn(cid="c1", source="auth", target_service="payment", endpoint="POST /charges")
    ep = _ep(repo_id="payment", method="POST", path="/charges")
    merged = _merged(connections=[conn], endpoints=[ep])

    n = link_connections_to_endpoints(merged)

    assert n == 1
    assert merged.connections["c1"].target_endpoint_id == ep.id


def test_method_uppercase_normalization() -> None:
    # Datadog typically uppercases methods, but be defensive.
    conn = _conn(cid="c1", source="auth", target_service="payment", endpoint="post /charges")
    ep = _ep(repo_id="payment", method="POST", path="/charges")
    merged = _merged(connections=[conn], endpoints=[ep])

    link_connections_to_endpoints(merged)
    assert merged.connections["c1"].target_endpoint_id == ep.id


def test_skips_when_target_service_unknown() -> None:
    # Infra-target connection (DB host) — no service to scope endpoints by.
    conn = _conn(cid="c1", source="auth", target_service=None, endpoint="SELECT users")
    ep = _ep(repo_id="payment", method="POST", path="/charges")
    merged = _merged(connections=[conn], endpoints=[ep])

    n = link_connections_to_endpoints(merged)
    assert n == 0
    assert merged.connections["c1"].target_endpoint_id is None


def test_skips_when_endpoint_not_http_shape() -> None:
    # 'SELECT users' is not 'METHOD /path' — never matchable to an HTTP Endpoint.
    conn = _conn(cid="c1", source="auth", target_service="payment", endpoint="SELECT users")
    ep = _ep(repo_id="payment", method="POST", path="/charges")
    merged = _merged(connections=[conn], endpoints=[ep])

    n = link_connections_to_endpoints(merged)
    assert n == 0


def test_skips_when_no_endpoint_matches_path() -> None:
    conn = _conn(cid="c1", source="auth", target_service="payment", endpoint="GET /missing")
    ep = _ep(repo_id="payment", method="POST", path="/charges")
    merged = _merged(connections=[conn], endpoints=[ep])

    n = link_connections_to_endpoints(merged)
    assert n == 0
    assert merged.connections["c1"].target_endpoint_id is None


def test_no_endpoints_no_op() -> None:
    conn = _conn(cid="c1", source="auth", target_service="payment", endpoint="POST /x")
    merged = _merged(connections=[conn], endpoints=[])
    n = link_connections_to_endpoints(merged)
    assert n == 0


def test_does_not_cross_service_boundaries() -> None:
    # Same method+path exists in TWO services — only the target-service endpoint matches.
    conn = _conn(cid="c1", source="auth", target_service="payment", endpoint="POST /charges")
    wrong = _ep(repo_id="billing", method="POST", path="/charges", eid="ep:billing")
    right = _ep(repo_id="payment", method="POST", path="/charges", eid="ep:payment")
    merged = _merged(connections=[conn], endpoints=[wrong, right])

    link_connections_to_endpoints(merged)
    assert merged.connections["c1"].target_endpoint_id == "ep:payment"


def test_suffix_match_against_unresolved_framework_prefix() -> None:
    # Real-world: testparser couldn't resolve the FastAPI APIRouter prefix and
    # stored it as a placeholder. Datadog reports the resolved path. The
    # suffix-match fallback should still link them.
    conn = _conn(
        cid="c1",
        source="auth",
        target_service="fba",
        endpoint="GET /sys/menus/42",
    )
    ep = _ep(
        repo_id="fba",
        method="GET",
        path="/<attr:settings.FASTAPI_API_V1_PATH>/sys/menus/42",
        eid="ep:menus",
    )
    merged = _merged(connections=[conn], endpoints=[ep])

    n = link_connections_to_endpoints(merged)
    assert n == 1
    assert merged.connections["c1"].target_endpoint_id == "ep:menus"


def test_suffix_match_does_not_fire_on_clean_paths() -> None:
    # If the indexed path has NO unresolved prefix, we don't blindly suffix-match
    # — that would let /v1/users/{id} eat a traced /users/{id}.
    conn = _conn(cid="c1", source="auth", target_service="svc", endpoint="GET /users/42")
    ep = _ep(repo_id="svc", method="GET", path="/v1/users/42", eid="ep:users")
    merged = _merged(connections=[conn], endpoints=[ep])

    n = link_connections_to_endpoints(merged)
    assert n == 0


def test_idempotent_when_already_linked() -> None:
    # Re-running should not double-count.
    conn = _conn(cid="c1", source="auth", target_service="payment", endpoint="POST /charges")
    ep = _ep(repo_id="payment", method="POST", path="/charges")
    merged = _merged(connections=[conn], endpoints=[ep])

    n1 = link_connections_to_endpoints(merged)
    n2 = link_connections_to_endpoints(merged)
    assert n1 == 1 and n2 == 0
