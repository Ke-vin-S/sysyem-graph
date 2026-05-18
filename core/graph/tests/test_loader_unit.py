"""Unit tests for GraphLoader using an in-memory session stub.

We deliberately do NOT mock the Cypher itself — that would just re-encode
the loader's implementation. Instead the stub records (statement, params)
pairs so we can assert structural behavior: idempotency MERGE shape,
batching, and edge emission.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from core.adapters.merger import MergedResult
from core.graph.loader import GraphLoader
from core.types import (
    CodeArtifact,
    Direction,
    ExternalConnection,
    LineRange,
    Service,
    TestCase,
    TestType,
)

NOW = datetime(2026, 5, 17, tzinfo=timezone.utc)


@dataclass
class _Recorded:
    cypher: str
    params: dict[str, Any]


@dataclass
class _StubSession:
    statements: list[_Recorded] = field(default_factory=list)
    by_label: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))

    def run(self, cypher: str, **params: Any) -> Any:
        self.statements.append(_Recorded(cypher=cypher, params=params))
        return _StubResult()

    def close(self) -> None:
        pass


@dataclass
class _StubResult:
    def consume(self) -> None:
        pass


@dataclass
class _StubClient:
    session_obj: _StubSession = field(default_factory=_StubSession)

    def session(self):
        import contextlib

        return contextlib.nullcontext(self.session_obj)


def _service(sid: str = "svc-a") -> Service:
    return Service(
        id=sid, name=sid, repoUrl=f"file://{sid}",
        createdAt=NOW, lastUpdatedAt=NOW, isActive=True,
    )


def _artifact(aid: str, repo: str = "svc-a") -> CodeArtifact:
    return CodeArtifact(
        id=aid, repoId=repo, type="function", name="foo",
        file="src/foo.py", lineRange=LineRange(start=1, end=1), isPublic=True,
    )


def _test_case(tid: str, repo: str = "svc-a", covers: tuple[str, ...] = ()) -> TestCase:
    return TestCase(
        id=tid, repoId=repo, type=TestType.UNIT, name="test_x",
        file="tests/x.py", lineRange=LineRange(start=1, end=2),
        coversArtifacts=covers,
    )


def _connection(cid: str = "conn-1", src: str = "svc-a", dst: str = "svc-b") -> ExternalConnection:
    return ExternalConnection(
        id=cid, type="http", sourceServiceId=src, targetServiceId=dst,
        targetName=dst, endpoint="POST /x", direction=Direction.OUTBOUND,
        discoveredAt=NOW, lastObservedAt=NOW,
    )


def _make_merged(*, services=None, artifacts=None, tests=None, connections=None) -> MergedResult:
    merged = MergedResult()
    for s in services or []:
        merged.services[s.id] = s
    for a in artifacts or []:
        merged.artifacts[a.id] = a
    for t in tests or []:
        merged.tests[t.id] = t
    for c in connections or []:
        merged.connections[c.id] = c
    return merged


def test_load_empty_merged_does_no_work() -> None:
    client = _StubClient()
    stats = GraphLoader(client).load(MergedResult())
    assert stats.services == 0 and stats.artifacts == 0 and stats.tests == 0
    assert client.session_obj.statements == []


def test_load_merges_services_with_id_predicate() -> None:
    client = _StubClient()
    merged = _make_merged(services=[_service("svc-a"), _service("svc-b")])
    stats = GraphLoader(client).load(merged)
    assert stats.services == 2
    service_stmts = [s for s in client.session_obj.statements if "MERGE (n:Service" in s.cypher]
    assert len(service_stmts) == 1  # single UNWIND batch
    assert "row.id" in service_stmts[0].cypher
    assert len(service_stmts[0].params["batch"]) == 2


def test_load_emits_contains_edges_for_artifacts() -> None:
    client = _StubClient()
    merged = _make_merged(
        services=[_service("svc-a")],
        artifacts=[_artifact("art-1", repo="svc-a"), _artifact("art-2", repo="svc-a")],
    )
    GraphLoader(client).load(merged)
    contains_stmts = [
        s for s in client.session_obj.statements if ":CONTAINS]" in s.cypher
    ]
    # CONTAINS now spans CodeArtifact + Endpoint + DataModel + Query buckets;
    # the only one with rows in this fixture is CodeArtifact.
    contains_stmts = [s for s in contains_stmts if s.params.get("batch")]
    assert len(contains_stmts) == 1
    rows = contains_stmts[0].params["batch"]
    assert {"art-1", "art-2"} == {r["dst"] for r in rows}
    assert all(r["src"] == "svc-a" for r in rows)


def test_load_emits_covers_edges_one_per_artifact_id() -> None:
    client = _StubClient()
    test = _test_case("test-1", covers=("art-1", "art-2", "art-3"))
    merged = _make_merged(
        services=[_service("svc-a")],
        artifacts=[_artifact("art-1"), _artifact("art-2"), _artifact("art-3")],
        tests=[test],
    )
    stats = GraphLoader(client).load(merged)
    assert stats.edges["COVERS"] == 3
    covers_stmts = [
        s for s in client.session_obj.statements if ":COVERS]" in s.cypher
    ]
    assert len(covers_stmts) == 1
    assert len(covers_stmts[0].params["batch"]) == 3


def test_load_emits_defines_edges() -> None:
    client = _StubClient()
    merged = _make_merged(
        services=[_service("svc-a")],
        tests=[_test_case("test-1"), _test_case("test-2")],
    )
    stats = GraphLoader(client).load(merged)
    assert stats.edges["DEFINES"] == 2


def test_load_emits_initiates_and_targets() -> None:
    client = _StubClient()
    merged = _make_merged(
        services=[_service("svc-a"), _service("svc-b")],
        connections=[_connection("c-1", src="svc-a", dst="svc-b")],
    )
    stats = GraphLoader(client).load(merged)
    assert stats.edges["INITIATES"] == 1
    assert stats.edges["TARGETS"] == 1


def test_load_skips_targets_for_external_resources() -> None:
    """ExternalConnection with no target_service_id (e.g. 3rd-party API)
    should not produce a TARGETS edge (until we add ExternalResource nodes
    in a later cut)."""
    client = _StubClient()
    conn = ExternalConnection(
        id="c", type="http", sourceServiceId="svc-a", targetServiceId=None,
        targetName="stripe", endpoint="POST /charges", direction=Direction.OUTBOUND,
        discoveredAt=NOW, lastObservedAt=NOW,
    )
    merged = _make_merged(services=[_service("svc-a")], connections=[conn])
    stats = GraphLoader(client).load(merged)
    assert stats.edges["INITIATES"] == 1
    assert stats.edges["TARGETS"] == 0


def test_load_handles_external_connections_in_artifacts() -> None:
    """artifact.external_connections produces EXPOSES edges."""
    client = _StubClient()
    artifact = CodeArtifact(
        id="art-1", repoId="svc-a", type="endpoint", name="POST /x",
        file="src/x.py", lineRange=LineRange(start=1, end=1), isPublic=True,
        externalConnections=("c-1",),
    )
    merged = _make_merged(
        services=[_service("svc-a")],
        artifacts=[artifact],
        connections=[_connection("c-1", src="svc-a", dst="svc-b")],
    )
    stats = GraphLoader(client).load(merged)
    assert stats.edges["EXPOSES"] == 1


@pytest.mark.parametrize(
    "n_records,expected_batches",
    [(0, 0), (1, 1), (500, 1), (501, 2), (1500, 3)],
)
def test_load_batches_at_500(n_records: int, expected_batches: int) -> None:
    client = _StubClient()
    services = [_service(f"svc-{i}") for i in range(n_records)]
    merged = _make_merged(services=services)
    GraphLoader(client).load(merged)
    batch_calls = [
        s for s in client.session_obj.statements if "MERGE (n:Service" in s.cypher
    ]
    assert len(batch_calls) == expected_batches
