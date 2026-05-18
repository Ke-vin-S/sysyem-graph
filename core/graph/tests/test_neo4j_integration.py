"""Integration tests against a live Neo4j.

Marked @pytest.mark.integration so the default test run skips them when
Neo4j is unreachable. The fixture wipes the DB before each test, so we
don't accumulate cross-test state.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.adapters.merger import MergedResult
from core.config import Neo4jSettings
from core.graph import (
    GraphLoader,
    GraphQueries,
    Migrator,
    Neo4jClient,
)
from core.graph.migrations.migrator import MIGRATIONS
from core.types import (
    CodeArtifact,
    Direction,
    ExternalConnection,
    LineRange,
    Service,
    TestCase,
    TestType,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 5, 17, tzinfo=timezone.utc)


@pytest.fixture
def client() -> Neo4jClient:
    """Yield a Neo4jClient backed by the configured DB, or skip if unreachable."""
    c = Neo4jClient(Neo4jSettings())
    if not c.healthcheck():
        pytest.skip(f"Neo4j unreachable at {c.uri}")
    Migrator().reset(c)  # clean slate per test
    Migrator().apply_pending(c)
    yield c
    Migrator().reset(c)
    c.close()


def _service(sid: str) -> Service:
    return Service(
        id=sid, name=sid, repoUrl=f"file://{sid}",
        createdAt=NOW, lastUpdatedAt=NOW, isActive=True,
    )


def _artifact(aid: str, *, repo: str, name: str, type_: str = "function") -> CodeArtifact:
    return CodeArtifact(
        id=aid, repoId=repo, type=type_, name=name,
        file=f"src/{name}.py", lineRange=LineRange(start=1, end=1), isPublic=True,
    )


def _test(tid: str, *, repo: str, name: str, covers: tuple[str, ...] = ()) -> TestCase:
    return TestCase(
        id=tid, repoId=repo, type=TestType.UNIT, name=name,
        file=f"tests/{name}.py", lineRange=LineRange(start=1, end=1),
        coversArtifacts=covers,
    )


def _conn(cid: str, *, src: str, dst: str) -> ExternalConnection:
    return ExternalConnection(
        id=cid, type="http", sourceServiceId=src, targetServiceId=dst,
        targetName=dst, endpoint="POST /x", direction=Direction.OUTBOUND,
        discoveredAt=NOW, lastObservedAt=NOW,
    )


def _merged(**kwargs) -> MergedResult:
    merged = MergedResult()
    for s in kwargs.get("services", []):
        merged.services[s.id] = s
    for a in kwargs.get("artifacts", []):
        merged.artifacts[a.id] = a
    for t in kwargs.get("tests", []):
        merged.tests[t.id] = t
    for c in kwargs.get("connections", []):
        merged.connections[c.id] = c
    return merged


def test_init_creates_constraints(client: Neo4jClient) -> None:
    """After apply_pending, querying for show constraints should list ours."""
    rows = client.run("SHOW CONSTRAINTS")
    names = {row.get("name") for row in rows}
    assert "service_id_unique" in names
    assert "test_case_id_unique" in names


def test_migrator_idempotent(client: Neo4jClient) -> None:
    """Running apply_pending twice does no extra work the second time."""
    first = Migrator().apply_pending(client)
    second = Migrator().apply_pending(client)
    # v1 always re-runs (idempotent bootstrap); higher versions are skipped.
    assert second.summary().startswith("1 applied")
    # One _SchemaMigration node per declared migration version (v1, v2, ...).
    rows = client.run("MATCH (m:_SchemaMigration) RETURN count(m) AS c")
    assert rows[0]["c"] == len(MIGRATIONS)
    _ = first  # silence unused


def test_load_writes_full_slice(client: Neo4jClient) -> None:
    merged = _merged(
        services=[_service("svc-a"), _service("svc-b")],
        artifacts=[
            _artifact("a-1", repo="svc-a", name="alpha"),
            _artifact("a-2", repo="svc-a", name="beta"),
            _artifact("a-3", repo="svc-b", name="gamma"),
        ],
        tests=[
            _test("t-1", repo="svc-a", name="test_alpha", covers=("a-1",)),
            _test("t-2", repo="svc-a", name="test_alpha_and_beta", covers=("a-1", "a-2")),
        ],
        connections=[_conn("c-1", src="svc-a", dst="svc-b")],
    )
    stats = GraphLoader(client).load(merged)
    assert stats.services == 2
    assert stats.artifacts == 3
    assert stats.tests == 2
    assert stats.connections == 1
    assert stats.edges["CONTAINS"] == 3
    assert stats.edges["DEFINES"] == 2
    assert stats.edges["COVERS"] == 3
    assert stats.edges["INITIATES"] == 1
    assert stats.edges["TARGETS"] == 1


def test_load_is_idempotent(client: Neo4jClient) -> None:
    """Loading the same MergedResult twice produces the same graph
    (no duplicate nodes or relationships)."""
    merged = _merged(
        services=[_service("svc-a")],
        artifacts=[_artifact("a-1", repo="svc-a", name="alpha")],
        tests=[_test("t-1", repo="svc-a", name="test_alpha", covers=("a-1",))],
    )
    loader = GraphLoader(client)
    loader.load(merged)
    loader.load(merged)
    queries = GraphQueries(client)
    counts = queries.counts()
    assert counts.services == 1
    assert counts.artifacts == 1
    assert counts.tests == 1
    assert counts.edges["COVERS"] == 1
    assert counts.edges["CONTAINS"] == 1


def test_queries_tests_covering(client: Neo4jClient) -> None:
    merged = _merged(
        services=[_service("svc-a")],
        artifacts=[_artifact("a-1", repo="svc-a", name="alpha")],
        tests=[
            _test("t-1", repo="svc-a", name="test_one", covers=("a-1",)),
            _test("t-2", repo="svc-a", name="test_two", covers=("a-1",)),
            _test("t-3", repo="svc-a", name="test_three"),  # doesn't cover a-1
        ],
    )
    GraphLoader(client).load(merged)
    covers = GraphQueries(client).tests_covering("a-1")
    names = {t.name for t in covers}
    assert names == {"test_one", "test_two"}


def test_queries_artifacts_covered_by(client: Neo4jClient) -> None:
    merged = _merged(
        services=[_service("svc-a")],
        artifacts=[
            _artifact("a-1", repo="svc-a", name="alpha"),
            _artifact("a-2", repo="svc-a", name="beta"),
        ],
        tests=[_test("t-1", repo="svc-a", name="test_both", covers=("a-1", "a-2"))],
    )
    GraphLoader(client).load(merged)
    covered = GraphQueries(client).artifacts_covered_by("t-1")
    names = {a.name for a in covered}
    assert names == {"alpha", "beta"}


def test_queries_service_endpoints(client: Neo4jClient) -> None:
    merged = _merged(
        services=[_service("svc-a")],
        artifacts=[
            _artifact("a-1", repo="svc-a", name="alpha", type_="endpoint"),
            _artifact("a-2", repo="svc-a", name="beta"),  # function, not endpoint
        ],
    )
    GraphLoader(client).load(merged)
    endpoints = GraphQueries(client).service_endpoints("svc-a")
    assert len(endpoints) == 1 and endpoints[0].name == "alpha"


def test_queries_services_calling(client: Neo4jClient) -> None:
    merged = _merged(
        services=[_service("svc-a"), _service("svc-b"), _service("svc-c")],
        connections=[
            _conn("c-ab", src="svc-a", dst="svc-b"),
            _conn("c-cb", src="svc-c", dst="svc-b"),
        ],
    )
    GraphLoader(client).load(merged)
    callers = GraphQueries(client).services_calling("svc-b")
    assert set(callers) == {"svc-a", "svc-c"}


def test_uniqueness_constraint_enforced(client: Neo4jClient) -> None:
    """Trying to CREATE (not MERGE) a duplicate Service must fail.
    Validates the schema actually got applied."""
    client.run(
        "CREATE (s:Service {id: $id, name: $id, repo_url: '', language: '', "
        "framework: '', owner: '', created_at: '', last_updated_at: '', is_active: true})",
        id="dupe",
    )
    with pytest.raises(Exception):
        client.run(
            "CREATE (s:Service {id: $id, name: $id, repo_url: '', language: '', "
            "framework: '', owner: '', created_at: '', last_updated_at: '', is_active: true})",
            id="dupe",
        )
