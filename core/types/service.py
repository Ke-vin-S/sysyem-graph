"""Core graph node types: Service, ExternalConnection, CodeArtifact, TestCase."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Criticality(StrEnum):
    """How important a dependency is to the calling service's correctness."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ContractStatus(StrEnum):
    """State of the agreement between two services on a connection."""

    STABLE = "STABLE"
    EVOLVING = "EVOLVING"
    DEPRECATED = "DEPRECATED"
    UNKNOWN = "UNKNOWN"


class Direction(StrEnum):
    """Direction of an external connection relative to the source service."""

    OUTBOUND = "OUTBOUND"
    INBOUND = "INBOUND"
    BIDIRECTIONAL = "BIDIRECTIONAL"


class TestType(StrEnum):
    """Test pyramid classification."""

    UNIT = "UNIT"
    COMPONENT = "COMPONENT"
    INTEGRATION = "INTEGRATION"
    E2E = "E2E"
    UNKNOWN = "UNKNOWN"


class _Frozen(BaseModel):
    """Base model: immutable, strict, JSON-serializable."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        populate_by_name=True,
    )


class _Provenance(_Frozen):
    """Base for graph nodes that carry pipeline provenance.

    `produced_by` is the pass name (e.g. "test_resolver") that emitted the
    record. `from_facts` is the Fact IDs consumed to derive it; for nodes
    derived from many facts, resolvers may store a summary token instead
    (e.g. ("symbol_count:127",)) to keep the property bounded.

    Both fields default to empty so existing JSON dumps load without
    rewriting — values arrive as resolvers are migrated pass by pass.
    """

    produced_by: str = Field(default="", alias="producedBy")
    from_facts: tuple[str, ...] = Field(default_factory=tuple, alias="fromFacts")


class LineRange(_Frozen):
    """Inclusive line range within a source file."""

    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @field_validator("end")
    @classmethod
    def _end_ge_start(cls, end: int, info) -> int:  # type: ignore[no-untyped-def]
        start = info.data.get("start")
        if start is not None and end < start:
            raise ValueError(f"LineRange.end ({end}) must be >= start ({start})")
        return end


class Service(_Provenance):
    """A deployable unit owning a repository.

    A Service is the top-level node in the impact graph. Every ExternalConnection
    is INITIATED by exactly one Service and TARGETS another Service (or external
    resource). Every CodeArtifact and TestCase is CONTAINED in exactly one Service.
    """

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    repo_url: str = Field(min_length=1, alias="repoUrl")
    language: str = Field(default="unknown")
    framework: str = Field(default="unknown")
    owner: str = Field(default="unknown")
    created_at: datetime = Field(alias="createdAt")
    last_updated_at: datetime = Field(alias="lastUpdatedAt")
    is_active: bool = Field(default=True, alias="isActive")


class ExternalConnection(_Provenance):
    """A directed call between two services (or to an external resource).

    Sourced primarily from Datadog APM traces but also discoverable via static
    analysis (e.g. an HTTP client call site in source code). Confidence is
    higher when traced than when inferred statically.
    """

    id: str = Field(min_length=1)
    type: str
    """Transport family: 'http', 'grpc', 'kafka', 'sql', 'redis', etc."""

    source_service_id: str = Field(alias="sourceServiceId", min_length=1)
    target_service_id: str | None = Field(default=None, alias="targetServiceId")
    target_name: str = Field(alias="targetName", min_length=1)
    """Human-readable target name. Used when target_service_id is unknown (third-party)."""

    target_endpoint_id: str | None = Field(default=None, alias="targetEndpointId")
    """The specific Endpoint this connection hits, when known. Populated by the
    post-merge enrichment pass that joins traced `endpoint` strings against
    static-analysis Endpoint nodes by (target_service, method, path)."""

    protocol: str = Field(default="unknown")
    endpoint: str = Field(default="")
    """For HTTP: 'METHOD /path'. For gRPC: 'service/Method'. For Kafka: topic name."""

    direction: Direction = Direction.OUTBOUND
    frequency: float = Field(default=0.0, ge=0.0)
    """Average calls per minute over the observation window."""

    criticality: Criticality = Criticality.MEDIUM
    contract_status: ContractStatus = Field(default=ContractStatus.UNKNOWN, alias="contractStatus")
    data_flow: dict[str, str] = Field(default_factory=dict, alias="dataFlow")
    """Free-form metadata about what data flows over this connection."""

    discovered_at: datetime = Field(alias="discoveredAt")
    last_observed_at: datetime = Field(alias="lastObservedAt")


class CodeArtifact(_Provenance):
    """A function, endpoint, schema, or other code-level object in a repo.

    CodeArtifacts are populated by source-code analysis adapters. When a commit
    modifies a file, the changed CodeArtifacts determine which ExternalConnections
    are potentially affected (a CodeArtifact may EXPOSE one or more connections).
    """

    id: str = Field(min_length=1)
    repo_id: str = Field(alias="repoId", min_length=1)
    type: str
    """'function', 'endpoint', 'schema', 'class', 'message', etc."""

    name: str = Field(min_length=1)
    file: str = Field(min_length=1)
    line_range: LineRange = Field(alias="lineRange")
    is_public: bool = Field(default=False, alias="isPublic")
    version: str | None = None
    external_connections: tuple[str, ...] = Field(default_factory=tuple, alias="externalConnections")
    """IDs of ExternalConnections this artifact exposes or calls."""

    calls: tuple[str, ...] = Field(default_factory=tuple)
    """IDs of CodeArtifacts this function/method invokes. Populated by
    FunctionCallResolver. Materialized as (CodeArtifact)-[:CALLS]->(CodeArtifact)
    edges in Neo4j."""


class TestCase(_Provenance):
    """A single test case identified in a repo.

    TestCases are the leaves of the impact graph: when a change affects a Service,
    the system answers 'which TestCases COVER artifacts in that Service or DEPEND_ON
    affected ExternalConnections?'
    """

    id: str = Field(min_length=1)
    repo_id: str = Field(alias="repoId", min_length=1)
    type: TestType = TestType.UNKNOWN
    name: str = Field(min_length=1)
    file: str = Field(min_length=1)
    line_range: LineRange = Field(alias="lineRange")
    duration_ms: int = Field(default=0, ge=0)
    flakiness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    priority: str = Field(default="MEDIUM")
    affected_repos: tuple[str, ...] = Field(default_factory=tuple, alias="affectedRepos")
    """Repo IDs this test exercises (its own, plus any it integration-tests against)."""

    covers_artifacts: tuple[str, ...] = Field(default_factory=tuple, alias="coversArtifacts")
    """IDs of CodeArtifacts this test exercises. Populated by CoverageResolver
    from the test file's IMPORT facts. Materialized as (TestCase)-[:COVERS]->
    (CodeArtifact) edges in Neo4j."""
