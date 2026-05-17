"""High-level read API.

Wraps Cypher so callers don't write strings themselves. Returns plain
dataclasses, not driver records, so the rest of the system never depends on
neo4j-driver types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.graph.client import Neo4jClient


@dataclass(frozen=True)
class GraphCounts:
    services: int
    artifacts: int
    tests: int
    connections: int
    edges: dict[str, int]


@dataclass(frozen=True)
class ArtifactSummary:
    id: str
    repo_id: str
    type: str
    name: str
    file: str


@dataclass(frozen=True)
class TestSummary:
    id: str
    repo_id: str
    type: str
    name: str
    file: str


@dataclass(frozen=True)
class ImpactedService:
    """Result of a transitive-dependents query.

    `via` lists the immediate predecessor's service IDs, useful for showing
    the chain when explaining impact to a user.
    """

    service_id: str
    depth: int


class GraphQueries:
    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    def counts(self) -> GraphCounts:
        node_counts: dict[str, int] = {}
        for label in ("Service", "CodeArtifact", "TestCase", "ExternalConnection"):
            rows = self._client.run(f"MATCH (n:{label}) RETURN count(n) AS c")
            node_counts[label] = int(rows[0]["c"]) if rows else 0

        edges: dict[str, int] = {}
        for rel in ("CONTAINS", "DEFINES", "COVERS", "INITIATES", "TARGETS", "EXPOSES"):
            rows = self._client.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c")
            edges[rel] = int(rows[0]["c"]) if rows else 0

        return GraphCounts(
            services=node_counts["Service"],
            artifacts=node_counts["CodeArtifact"],
            tests=node_counts["TestCase"],
            connections=node_counts["ExternalConnection"],
            edges=edges,
        )

    def tests_covering(self, artifact_id: str) -> list[TestSummary]:
        rows = self._client.run(
            "MATCH (t:TestCase)-[:COVERS]->(a:CodeArtifact {id: $id}) "
            "RETURN t.id AS id, t.repo_id AS repo_id, t.type AS type, "
            "       t.name AS name, t.file AS file "
            "ORDER BY t.repo_id, t.file, t.name",
            id=artifact_id,
        )
        return [TestSummary(**row) for row in rows]

    def artifacts_covered_by(self, test_id: str) -> list[ArtifactSummary]:
        rows = self._client.run(
            "MATCH (t:TestCase {id: $id})-[:COVERS]->(a:CodeArtifact) "
            "RETURN a.id AS id, a.repo_id AS repo_id, a.type AS type, "
            "       a.name AS name, a.file AS file "
            "ORDER BY a.type, a.file, a.name",
            id=test_id,
        )
        return [ArtifactSummary(**row) for row in rows]

    def service_endpoints(self, service_id: str) -> list[ArtifactSummary]:
        rows = self._client.run(
            "MATCH (:Service {id: $id})-[:CONTAINS]->(a:CodeArtifact {type: 'endpoint'}) "
            "RETURN a.id AS id, a.repo_id AS repo_id, a.type AS type, "
            "       a.name AS name, a.file AS file "
            "ORDER BY a.name",
            id=service_id,
        )
        return [ArtifactSummary(**row) for row in rows]

    def services_calling(self, service_id: str) -> list[str]:
        rows = self._client.run(
            "MATCH (caller:Service)-[:INITIATES]->(:ExternalConnection)"
            "-[:TARGETS]->(callee:Service {id: $id}) "
            "WHERE caller.id <> callee.id "
            "RETURN DISTINCT caller.id AS id ORDER BY caller.id",
            id=service_id,
        )
        return [row["id"] for row in rows]

    def transitive_dependents(
        self, service_id: str, *, max_depth: int = 5
    ) -> list[ImpactedService]:
        """Services that depend on `service_id`, up to `max_depth` hops.

        Direction: A->B means A INITIATES a connection that TARGETS B. So the
        services that depend ON `service_id` are the ones that, transitively
        through INITIATES/TARGETS, reach it.
        """
        rows = self._client.run(
            "MATCH path = (caller:Service)-[:INITIATES|TARGETS*1..%d]->(:Service {id: $id}) "
            "WHERE caller.id <> $id "
            "RETURN DISTINCT caller.id AS service_id, "
            "       length(path)/2 AS depth "
            "ORDER BY depth, service_id" % (max_depth * 2),
            id=service_id,
        )
        return [
            ImpactedService(service_id=row["service_id"], depth=int(row["depth"]))
            for row in rows
        ]

    def service(self, service_id: str) -> dict[str, Any] | None:
        rows = self._client.run(
            "MATCH (s:Service {id: $id}) RETURN properties(s) AS props",
            id=service_id,
        )
        return rows[0]["props"] if rows else None
