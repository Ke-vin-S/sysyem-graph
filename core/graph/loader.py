"""GraphLoader — writes Service/Artifact/Test/Connection nodes + edges into Neo4j.

Idempotency contract: re-running `load(merged)` against the same MergedResult
produces an identical graph. We MERGE on stable IDs everywhere, never CREATE.

Property shape: Neo4j only takes scalars and lists-of-scalars as property
values. Nested objects (LineRange, dict[str,str] data_flow) get flattened or
JSON-stringified. Tuples become lists. Datetimes become ISO strings. Enums
become their string values.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from core.adapters.merger import MergedResult
from core.graph.client import Neo4jClient
from core.types import (
    CodeArtifact,
    ExternalConnection,
    Service,
    TestCase,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


@dataclass
class LoadStats:
    services: int = 0
    artifacts: int = 0
    tests: int = 0
    connections: int = 0
    edges: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "Service": self.services,
            "CodeArtifact": self.artifacts,
            "TestCase": self.tests,
            "ExternalConnection": self.connections,
            **{f"({k})": v for k, v in self.edges.items()},
        }


class GraphLoader:
    """Loads a MergedResult into Neo4j with MERGE semantics."""

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    def load(self, merged: MergedResult) -> LoadStats:
        stats = LoadStats()
        services = list(merged.services.values())
        artifacts = list(merged.artifacts.values())
        tests = list(merged.tests.values())
        connections = list(merged.connections.values())

        with self._client.session() as session:
            stats.services = self._merge_nodes(
                session,
                label="Service",
                rows=[_service_props(s) for s in services],
            )
            stats.artifacts = self._merge_nodes(
                session,
                label="CodeArtifact",
                rows=[_artifact_props(a) for a in artifacts],
            )
            stats.tests = self._merge_nodes(
                session,
                label="TestCase",
                rows=[_test_props(t) for t in tests],
            )
            stats.connections = self._merge_nodes(
                session,
                label="ExternalConnection",
                rows=[_connection_props(c) for c in connections],
            )

            stats.edges["CONTAINS"] = self._merge_edges(
                session,
                src_label="Service",
                rel="CONTAINS",
                dst_label="CodeArtifact",
                rows=[{"src": a.repo_id, "dst": a.id} for a in artifacts],
            )
            stats.edges["DEFINES"] = self._merge_edges(
                session,
                src_label="Service",
                rel="DEFINES",
                dst_label="TestCase",
                rows=[{"src": t.repo_id, "dst": t.id} for t in tests],
            )
            stats.edges["COVERS"] = self._merge_edges(
                session,
                src_label="TestCase",
                rel="COVERS",
                dst_label="CodeArtifact",
                rows=[
                    {"src": t.id, "dst": artifact_id}
                    for t in tests
                    for artifact_id in t.covers_artifacts
                ],
            )
            stats.edges["INITIATES"] = self._merge_edges(
                session,
                src_label="Service",
                rel="INITIATES",
                dst_label="ExternalConnection",
                rows=[{"src": c.source_service_id, "dst": c.id} for c in connections],
            )
            stats.edges["TARGETS"] = self._merge_edges(
                session,
                src_label="ExternalConnection",
                rel="TARGETS",
                dst_label="Service",
                rows=[
                    {"src": c.id, "dst": c.target_service_id}
                    for c in connections
                    if c.target_service_id
                ],
            )
            stats.edges["EXPOSES"] = self._merge_edges(
                session,
                src_label="CodeArtifact",
                rel="EXPOSES",
                dst_label="ExternalConnection",
                rows=[
                    {"src": a.id, "dst": conn_id}
                    for a in artifacts
                    for conn_id in a.external_connections
                ],
            )

        return stats

    def _merge_nodes(self, session: Any, *, label: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        cypher = (
            f"UNWIND $batch AS row "
            f"MERGE (n:{label} {{id: row.id}}) "
            f"SET n += row"
        )
        written = 0
        for batch in _chunks(rows, _BATCH_SIZE):
            session.run(cypher, batch=batch).consume()
            written += len(batch)
        return written

    def _merge_edges(
        self,
        session: Any,
        *,
        src_label: str,
        rel: str,
        dst_label: str,
        rows: list[dict[str, str]],
    ) -> int:
        if not rows:
            return 0
        cypher = (
            "UNWIND $batch AS row "
            f"MATCH (a:{src_label} {{id: row.src}}) "
            f"MATCH (b:{dst_label} {{id: row.dst}}) "
            f"MERGE (a)-[:{rel}]->(b)"
        )
        written = 0
        for batch in _chunks(rows, _BATCH_SIZE):
            session.run(cypher, batch=batch).consume()
            written += len(batch)
        return written


# ---- property shaping ---------------------------------------------------


def _service_props(s: Service) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "repo_url": s.repo_url,
        "language": s.language,
        "framework": s.framework,
        "owner": s.owner,
        "created_at": s.created_at.isoformat(),
        "last_updated_at": s.last_updated_at.isoformat(),
        "is_active": s.is_active,
    }


def _artifact_props(a: CodeArtifact) -> dict[str, Any]:
    return {
        "id": a.id,
        "repo_id": a.repo_id,
        "type": a.type,
        "name": a.name,
        "file": a.file,
        "line_start": a.line_range.start,
        "line_end": a.line_range.end,
        "is_public": a.is_public,
        "version": a.version or "",
    }


def _test_props(t: TestCase) -> dict[str, Any]:
    return {
        "id": t.id,
        "repo_id": t.repo_id,
        "type": t.type.value,
        "name": t.name,
        "file": t.file,
        "line_start": t.line_range.start,
        "line_end": t.line_range.end,
        "duration_ms": t.duration_ms,
        "flakiness_score": t.flakiness_score,
        "priority": t.priority,
        "affected_repos": list(t.affected_repos),
    }


def _connection_props(c: ExternalConnection) -> dict[str, Any]:
    return {
        "id": c.id,
        "type": c.type,
        "source_service_id": c.source_service_id,
        "target_service_id": c.target_service_id or "",
        "target_name": c.target_name,
        "protocol": c.protocol,
        "endpoint": c.endpoint,
        "direction": c.direction.value,
        "frequency": c.frequency,
        "criticality": c.criticality.value,
        "contract_status": c.contract_status.value,
        "data_flow_json": json.dumps(c.data_flow, sort_keys=True),
        "discovered_at": c.discovered_at.isoformat(),
        "last_observed_at": c.last_observed_at.isoformat(),
    }


def _chunks(items: Iterable[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    buf: list[dict[str, Any]] = []
    for item in items:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf
