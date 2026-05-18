"""IngestionAdapter abstract base class and supporting types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.types import (
    Change,
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
    TestCase,
)


@dataclass
class IngestionContext:
    """Per-run context passed to every adapter.

    Adapters are pure-ish functions of (config, context) -> AdapterResult. The
    context carries cross-cutting state — wall-clock 'now', user-supplied
    repo allowlist, dry-run flag — that should not be baked into adapter config.
    """

    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    repos: tuple[str, ...] = ()
    """Optional allowlist of repos to scan. Empty tuple = no filter."""

    dry_run: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class Coverage:
    """How much of the universe an adapter saw on this run.

    Used by the confidence scorer to weight an adapter's contributions: an
    adapter that scanned 95% of services produces edges we trust more than one
    that scanned 30%.
    """

    services_scanned: int = 0
    services_total: int | None = None
    notes: str = ""

    @property
    def ratio(self) -> float:
        if not self.services_total:
            return 1.0 if self.services_scanned else 0.0
        return min(1.0, self.services_scanned / self.services_total)


@dataclass
class AdapterResult:
    """Everything an adapter produces in one run.

    Adapters return self-contained results; the registry merges them. Adapters
    must not write directly to Neo4j — the loader is the only writer.
    """

    adapter: str
    services: list[Service] = field(default_factory=list)
    connections: list[ExternalConnection] = field(default_factory=list)
    artifacts: list[CodeArtifact] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    data_models: list[DataModel] = field(default_factory=list)
    queries: list[Query] = field(default_factory=list)
    kafka_topics: list[KafkaTopic] = field(default_factory=list)
    kafka_producers: list[KafkaProducer] = field(default_factory=list)
    kafka_consumers: list[KafkaConsumer] = field(default_factory=list)
    mocks: list[Mock] = field(default_factory=list)
    tests: list[TestCase] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    warnings: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def is_empty(self) -> bool:
        return not (
            self.services
            or self.connections
            or self.artifacts
            or self.endpoints
            or self.data_models
            or self.queries
            or self.kafka_topics
            or self.kafka_producers
            or self.kafka_consumers
            or self.mocks
            or self.tests
            or self.changes
        )

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
            "changes": len(self.changes),
        }


class IngestionAdapter(ABC):
    """Abstract base for all ingestion adapters.

    Each concrete adapter (Datadog, GitHub, test parser, ...) implements
    extract() to fetch from its source and return a fully-typed AdapterResult.
    The default validate() runs Pydantic validation by construction; subclasses
    can override to add source-specific checks.
    """

    #: Stable, lowercase identifier (e.g. 'datadog'). Used in logs and metrics.
    name: str = ""

    #: Higher priority runs first. Adapters that produce ground truth (Datadog
    #: traces) should outrank inferential ones (static-analysis-derived edges).
    priority: int = 0

    @abstractmethod
    def extract(self, context: IngestionContext) -> AdapterResult:
        """Fetch and return data. Must not write to the graph."""

    def validate(self, result: AdapterResult) -> list[str]:
        """Return a list of human-readable warnings (empty = clean).

        Pydantic already enforces field-level validity. This hook is for
        cross-record checks: dangling connection targets, duplicate IDs, etc.
        """
        warnings: list[str] = []
        seen_service_ids: set[str] = set()
        for service in result.services:
            if service.id in seen_service_ids:
                warnings.append(f"duplicate service id: {service.id}")
            seen_service_ids.add(service.id)
        return warnings

    def get_coverage(self, result: AdapterResult) -> Coverage:
        """Default: defer to whatever extract() filled in."""
        return result.coverage

    def get_identifier(self) -> str:
        return self.name or type(self).__name__.lower()

    def get_version(self) -> str:
        return getattr(self, "version", "0.1.0")
