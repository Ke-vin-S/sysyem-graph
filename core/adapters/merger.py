"""Merge AdapterResults from multiple adapters into a single deduplicated view."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.adapters.base import AdapterResult
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


@dataclass
class MergedResult:
    services: dict[str, Service] = field(default_factory=dict)
    connections: dict[str, ExternalConnection] = field(default_factory=dict)
    artifacts: dict[str, CodeArtifact] = field(default_factory=dict)
    endpoints: dict[str, Endpoint] = field(default_factory=dict)
    data_models: dict[str, DataModel] = field(default_factory=dict)
    queries: dict[str, Query] = field(default_factory=dict)
    kafka_topics: dict[str, KafkaTopic] = field(default_factory=dict)
    kafka_producers: dict[str, KafkaProducer] = field(default_factory=dict)
    kafka_consumers: dict[str, KafkaConsumer] = field(default_factory=dict)
    mocks: dict[str, Mock] = field(default_factory=dict)
    tests: dict[str, TestCase] = field(default_factory=dict)
    suggestions: dict[str, Suggestion] = field(default_factory=dict)
    """LLM-emitted candidate edges (source='llm'). Loaded as tagged edges."""

    conflicts: list[str] = field(default_factory=list)
    """Human-readable notes about which adapter won each conflict."""

    def counts(self) -> dict[str, int]:
        return {
            "services": len(self.services),
            "connections": len(self.connections),
            "artifacts": len(self.artifacts),
            "endpoints": len(self.endpoints),
            "data_models": len(self.data_models),
            "queries": len(self.queries),
            "kafka_topics": len(self.kafka_topics),
            "kafka_producers": len(self.kafka_producers),
            "kafka_consumers": len(self.kafka_consumers),
            "mocks": len(self.mocks),
            "tests": len(self.tests),
            "suggestions": len(self.suggestions),
            "conflicts": len(self.conflicts),
        }


class ResultMerger:
    """Deduplicate by ID; on conflict, the higher-priority adapter wins.

    Adapters arrive pre-sorted by descending priority from the registry, so
    we just keep the first occurrence and record any losers in `conflicts`.

    KafkaTopic is the one cross-adapter join key: multiple adapters can
    legitimately emit the same topic, and the dedupe keeps a single node.
    """

    def merge(self, results: list[AdapterResult]) -> MergedResult:
        merged = MergedResult()
        for result in results:
            _accumulate(merged.services, result.services, result.adapter, "service", merged.conflicts)
            _accumulate(
                merged.connections,
                result.connections,
                result.adapter,
                "connection",
                merged.conflicts,
            )
            _accumulate(merged.artifacts, result.artifacts, result.adapter, "artifact", merged.conflicts)
            _accumulate(merged.endpoints, result.endpoints, result.adapter, "endpoint", merged.conflicts)
            _accumulate(
                merged.data_models, result.data_models, result.adapter, "data_model", merged.conflicts
            )
            _accumulate(merged.queries, result.queries, result.adapter, "query", merged.conflicts)
            _accumulate(
                merged.kafka_topics,
                result.kafka_topics,
                result.adapter,
                "kafka_topic",
                merged.conflicts,
            )
            _accumulate(
                merged.kafka_producers,
                result.kafka_producers,
                result.adapter,
                "kafka_producer",
                merged.conflicts,
            )
            _accumulate(
                merged.kafka_consumers,
                result.kafka_consumers,
                result.adapter,
                "kafka_consumer",
                merged.conflicts,
            )
            _accumulate(merged.mocks, result.mocks, result.adapter, "mock", merged.conflicts)
            _accumulate(merged.tests, result.tests, result.adapter, "test", merged.conflicts)
        return merged


def _accumulate(
    bucket: dict[str, object],
    items: list,  # type: ignore[type-arg]
    adapter: str,
    kind: str,
    conflicts: list[str],
) -> None:
    for item in items:
        item_id = item.id  # type: ignore[attr-defined]
        if item_id in bucket:
            # KafkaTopic dedupe is silent: it's expected, not a conflict.
            if kind != "kafka_topic":
                conflicts.append(
                    f"{kind} {item_id}: kept higher-priority value; {adapter} skipped"
                )
            continue
        bucket[item_id] = item
