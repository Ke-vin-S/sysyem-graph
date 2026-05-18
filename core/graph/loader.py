"""GraphLoader — writes Service/Artifact/Test/Connection nodes + edges into Neo4j.

Idempotency contract: re-running `load(merged)` against the same MergedResult
produces an identical graph. We MERGE on stable IDs everywhere, never CREATE.

Property shape: Neo4j only takes scalars and lists-of-scalars as property
values. Nested objects (LineRange, dict[str,str] data_flow) get flattened or
JSON-stringified. Tuples become lists. Datetimes become ISO strings. Enums
become their string values.

Edge provenance: every relationship MERGE'd here sets `source` and
`confidence` properties so downstream queries can distinguish deterministic
edges (`source="resolver"`, `confidence=1.0`) from LLM-suggested ones
(`source="llm"`, confidence < 1.0). The default for resolver-emitted edges
is (`"resolver"`, 1.0); the LLM enhance pass overrides both.
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
    DataModel,
    Endpoint,
    ExternalConnection,
    KafkaConsumer,
    KafkaProducer,
    KafkaTopic,
    Mock,
    Query,
    Service,
    Suggestion,
    TestCase,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500

_RESOLVER_SOURCE = "resolver"
_RESOLVER_CONFIDENCE = 1.0
_LLM_SOURCE = "llm"


@dataclass
class LoadStats:
    services: int = 0
    artifacts: int = 0
    tests: int = 0
    connections: int = 0
    endpoints: int = 0
    data_models: int = 0
    queries: int = 0
    kafka_topics: int = 0
    kafka_producers: int = 0
    kafka_consumers: int = 0
    mocks: int = 0
    edges: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "Service": self.services,
            "CodeArtifact": self.artifacts,
            "TestCase": self.tests,
            "ExternalConnection": self.connections,
            "Endpoint": self.endpoints,
            "DataModel": self.data_models,
            "Query": self.queries,
            "KafkaTopic": self.kafka_topics,
            "KafkaProducer": self.kafka_producers,
            "KafkaConsumer": self.kafka_consumers,
            "Mock": self.mocks,
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
        endpoints = list(merged.endpoints.values())
        data_models = list(merged.data_models.values())
        queries = list(merged.queries.values())
        kafka_topics = list(merged.kafka_topics.values())
        kafka_producers = list(merged.kafka_producers.values())
        kafka_consumers = list(merged.kafka_consumers.values())
        mocks = list(merged.mocks.values())

        with self._client.session() as session:
            # ---- Phase-1 nodes ----------------------------------------
            stats.services = self._merge_nodes(
                session, label="Service", rows=[_service_props(s) for s in services]
            )
            stats.artifacts = self._merge_nodes(
                session, label="CodeArtifact", rows=[_artifact_props(a) for a in artifacts]
            )
            stats.tests = self._merge_nodes(
                session, label="TestCase", rows=[_test_props(t) for t in tests]
            )
            stats.connections = self._merge_nodes(
                session,
                label="ExternalConnection",
                rows=[_connection_props(c) for c in connections],
            )

            # ---- Phase-2 nodes ----------------------------------------
            stats.endpoints = self._merge_nodes(
                session, label="Endpoint", rows=[_endpoint_props(e) for e in endpoints]
            )
            stats.data_models = self._merge_nodes(
                session, label="DataModel", rows=[_data_model_props(d) for d in data_models]
            )
            stats.queries = self._merge_nodes(
                session, label="Query", rows=[_query_props(q) for q in queries]
            )
            stats.kafka_topics = self._merge_nodes(
                session, label="KafkaTopic", rows=[_kafka_topic_props(t) for t in kafka_topics]
            )
            stats.kafka_producers = self._merge_nodes(
                session,
                label="KafkaProducer",
                rows=[_kafka_producer_props(p) for p in kafka_producers],
            )
            stats.kafka_consumers = self._merge_nodes(
                session,
                label="KafkaConsumer",
                rows=[_kafka_consumer_props(c) for c in kafka_consumers],
            )
            stats.mocks = self._merge_nodes(
                session, label="Mock", rows=[_mock_props(m) for m in mocks]
            )

            # ---- Phase-1 edges ----------------------------------------
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
            stats.edges["CALLS"] = self._merge_edges(
                session,
                src_label="CodeArtifact",
                rel="CALLS",
                dst_label="CodeArtifact",
                rows=[
                    {"src": a.id, "dst": callee_id}
                    for a in artifacts
                    for callee_id in a.calls
                ],
            )

            # ---- Phase-2 edges ----------------------------------------
            # (Service)-[:CONTAINS]->(Endpoint|DataModel|Query|Mock|KafkaP/C)
            stats.edges["CONTAINS_ENDPOINT"] = self._merge_edges(
                session,
                src_label="Service",
                rel="CONTAINS",
                dst_label="Endpoint",
                rows=[{"src": e.repo_id, "dst": e.id} for e in endpoints],
            )
            stats.edges["CONTAINS_DATA_MODEL"] = self._merge_edges(
                session,
                src_label="Service",
                rel="CONTAINS",
                dst_label="DataModel",
                rows=[{"src": d.repo_id, "dst": d.id} for d in data_models],
            )
            stats.edges["CONTAINS_QUERY"] = self._merge_edges(
                session,
                src_label="Service",
                rel="CONTAINS",
                dst_label="Query",
                rows=[{"src": q.repo_id, "dst": q.id} for q in queries],
            )
            stats.edges["HANDLED_BY"] = self._merge_edges(
                session,
                src_label="Endpoint",
                rel="HANDLED_BY",
                dst_label="CodeArtifact",
                rows=[
                    {"src": e.id, "dst": e.handler_artifact_id}
                    for e in endpoints
                    if e.handler_artifact_id
                ],
            )
            stats.edges["EXECUTES"] = self._merge_edges(
                session,
                src_label="CodeArtifact",
                rel="EXECUTES",
                dst_label="Query",
                rows=[
                    {"src": q.enclosing_artifact_id, "dst": q.id}
                    for q in queries
                    if q.enclosing_artifact_id
                ],
            )
            stats.edges["PRODUCES"] = self._merge_edges(
                session,
                src_label="KafkaProducer",
                rel="PRODUCES",
                dst_label="KafkaTopic",
                rows=[
                    {"src": p.id, "dst": _topic_id(p.topic_name)} for p in kafka_producers
                ],
            )
            stats.edges["CONSUMES"] = self._merge_edges(
                session,
                src_label="KafkaConsumer",
                rel="CONSUMES",
                dst_label="KafkaTopic",
                rows=[
                    {"src": c.id, "dst": _topic_id(c.topic_name)} for c in kafka_consumers
                ],
            )
            stats.edges["MOCKS"] = self._merge_edges(
                session,
                src_label="TestCase",
                rel="MOCKS",
                dst_label="CodeArtifact",
                rows=[
                    {"src": m.test_id, "dst": m.target_artifact_id}
                    for m in mocks
                    if m.target_artifact_id
                ],
            )

            # ---- LLM suggestion edges -------------------------------
            # Grouped by (rel, src_label, dst_label) since each combo needs
            # its own typed MATCH. source="llm" + the suggestion's own
            # confidence are stamped instead of the resolver defaults.
            suggestions = list(merged.suggestions.values())
            by_shape: dict[tuple[str, str, str], list[Suggestion]] = {}
            for sug in suggestions:
                by_shape.setdefault((sug.src_label, sug.rel, sug.dst_label), []).append(sug)
            for (src_label, rel, dst_label), bucket in by_shape.items():
                key = f"LLM_{rel}"
                stats.edges[key] = stats.edges.get(key, 0) + self._merge_edges(
                    session,
                    src_label=src_label,
                    rel=rel,
                    dst_label=dst_label,
                    rows=[
                        {
                            "src": s.src_id, "dst": s.dst_id,
                            "source": _LLM_SOURCE, "confidence": s.confidence,
                        }
                        for s in bucket
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
        """MERGE relationships, always stamping `source` and `confidence`.

        Rows are `{"src", "dst"}` (and optionally `"source"`, `"confidence"`).
        Anything not supplied falls back to the resolver defaults.
        """
        if not rows:
            return 0
        cypher = (
            "UNWIND $batch AS row "
            f"MATCH (a:{src_label} {{id: row.src}}) "
            f"MATCH (b:{dst_label} {{id: row.dst}}) "
            f"MERGE (a)-[r:{rel}]->(b) "
            "SET r.source = coalesce(row.source, $default_source), "
            "    r.confidence = coalesce(row.confidence, $default_confidence)"
        )
        written = 0
        for batch in _chunks(rows, _BATCH_SIZE):
            session.run(
                cypher,
                batch=batch,
                default_source=_RESOLVER_SOURCE,
                default_confidence=_RESOLVER_CONFIDENCE,
            ).consume()
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
        "produced_by": s.produced_by,
        "from_facts": list(s.from_facts),
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
        "produced_by": a.produced_by,
        "from_facts": list(a.from_facts),
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
        "produced_by": t.produced_by,
        "from_facts": list(t.from_facts),
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
        "produced_by": c.produced_by,
        "from_facts": list(c.from_facts),
    }


def _endpoint_props(e: Endpoint) -> dict[str, Any]:
    return {
        "id": e.id,
        "repo_id": e.repo_id,
        "method": e.method,
        "path": e.path,
        "framework": e.framework,
        "handler_file": e.handler_file,
        "handler_symbol": e.handler_symbol,
        "is_public": e.is_public,
        "produced_by": e.produced_by,
        "from_facts": list(e.from_facts),
    }


def _data_model_props(d: DataModel) -> dict[str, Any]:
    # Fields are (name, type) pairs — flatten to parallel lists for Neo4j.
    field_names = [f[0] for f in d.fields]
    field_types = [f[1] for f in d.fields]
    return {
        "id": d.id,
        "repo_id": d.repo_id,
        "name": d.name,
        "file": d.file,
        "line_start": d.line_range.start,
        "line_end": d.line_range.end,
        "kind": d.kind.value,
        "field_names": field_names,
        "field_types": field_types,
        "table_name": d.table_name,
        "is_public": d.is_public,
        "produced_by": d.produced_by,
        "from_facts": list(d.from_facts),
    }


def _query_props(q: Query) -> dict[str, Any]:
    return {
        "id": q.id,
        "repo_id": q.repo_id,
        "kind": q.kind.value,
        "file": q.file,
        "line": q.line,
        "expression": q.expression,
        "tables": list(q.tables),
        "produced_by": q.produced_by,
        "from_facts": list(q.from_facts),
    }


def _kafka_topic_props(t: KafkaTopic) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "produced_by": t.produced_by,
        "from_facts": list(t.from_facts),
    }


def _kafka_producer_props(p: KafkaProducer) -> dict[str, Any]:
    return {
        "id": p.id,
        "repo_id": p.repo_id,
        "function_artifact_id": p.function_artifact_id,
        "topic_name": p.topic_name,
        "file": p.file,
        "line": p.line,
        "framework": p.framework,
        "produced_by": p.produced_by,
        "from_facts": list(p.from_facts),
    }


def _kafka_consumer_props(c: KafkaConsumer) -> dict[str, Any]:
    return {
        "id": c.id,
        "repo_id": c.repo_id,
        "function_artifact_id": c.function_artifact_id,
        "topic_name": c.topic_name,
        "file": c.file,
        "line": c.line,
        "framework": c.framework,
        "consumer_group": c.consumer_group,
        "produced_by": c.produced_by,
        "from_facts": list(c.from_facts),
    }


def _mock_props(m: Mock) -> dict[str, Any]:
    return {
        "id": m.id,
        "repo_id": m.repo_id,
        "test_id": m.test_id,
        "kind": m.kind.value,
        "patch_target": m.patch_target,
        "target_artifact_id": m.target_artifact_id or "",
        "file": m.file,
        "line": m.line,
        "produced_by": m.produced_by,
        "from_facts": list(m.from_facts),
    }


def _topic_id(name: str) -> str:
    """Stable cross-repo id for a Kafka topic. Mirrors the convention used
    when a `KafkaTopic` is constructed by the resolver."""
    return f"topic:{name}"


def _chunks(items: Iterable[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    buf: list[dict[str, Any]] = []
    for item in items:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf
