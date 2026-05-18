"""Declarative schema: node labels, relationship types, and the indices we maintain.

Migrations consume this module. The schema is the contract between
ingestion JSON and Neo4j; if you change a label or rename a property,
write a new migration.
"""

from __future__ import annotations

from dataclasses import dataclass

NODE_LABELS: tuple[str, ...] = (
    # Phase-1 (initial schema, migration v1)
    "Service",
    "CodeArtifact",
    "TestCase",
    "ExternalConnection",
    "Change",
    "ExternalResource",
    # Phase-2 (migration v2)
    "Endpoint",
    "DataModel",
    "Query",
    "KafkaTopic",
    "KafkaProducer",
    "KafkaConsumer",
    "Mock",
)

RELATIONSHIPS: tuple[str, ...] = (
    # Phase-1
    "CONTAINS",     # (Service)-[:CONTAINS]->(CodeArtifact|Endpoint|DataModel|...)
    "DEFINES",      # (Service)-[:DEFINES]->(TestCase)
    "COVERS",       # (TestCase)-[:COVERS]->(CodeArtifact)
    "INITIATES",    # (Service)-[:INITIATES]->(ExternalConnection)
    "TARGETS",      # (ExternalConnection)-[:TARGETS]->(Service|ExternalResource)
    "EXPOSES",      # (CodeArtifact|Endpoint)-[:EXPOSES]->(ExternalConnection)
    "DEPENDS_ON",   # (TestCase)-[:DEPENDS_ON]->(ExternalConnection)
    # Phase-2
    "CALLS",        # (CodeArtifact)-[:CALLS]->(CodeArtifact)
    "HANDLED_BY",   # (Endpoint)-[:HANDLED_BY]->(CodeArtifact)
    "READS",        # (CodeArtifact)-[:READS]->(DataModel)
    "WRITES",       # (CodeArtifact)-[:WRITES]->(DataModel)
    "EXECUTES",     # (CodeArtifact)-[:EXECUTES]->(Query)
    "TOUCHES",      # (Query)-[:TOUCHES]->(DataModel)
    "PRODUCES",     # (CodeArtifact|KafkaProducer)-[:PRODUCES]->(KafkaTopic)
    "CONSUMES",     # (CodeArtifact|KafkaConsumer)-[:CONSUMES]->(KafkaTopic)
    "MOCKS",        # (TestCase)-[:MOCKS]->(CodeArtifact)  via Mock node
    "BOUND_TO",     # (Endpoint)-[:BOUND_TO]->(*)  config-driven bindings
)


@dataclass(frozen=True)
class Constraint:
    label: str
    property: str
    name: str

    @property
    def cypher(self) -> str:
        return (
            f"CREATE CONSTRAINT {self.name} IF NOT EXISTS "
            f"FOR (n:{self.label}) REQUIRE n.{self.property} IS UNIQUE"
        )

    @property
    def drop_cypher(self) -> str:
        return f"DROP CONSTRAINT {self.name} IF EXISTS"


@dataclass(frozen=True)
class Index:
    label: str
    property: str
    name: str

    @property
    def cypher(self) -> str:
        return (
            f"CREATE INDEX {self.name} IF NOT EXISTS "
            f"FOR (n:{self.label}) ON (n.{self.property})"
        )

    @property
    def drop_cypher(self) -> str:
        return f"DROP INDEX {self.name} IF EXISTS"


UNIQUENESS_CONSTRAINTS: tuple[Constraint, ...] = (
    # Phase-1
    Constraint(label="Service", property="id", name="service_id_unique"),
    Constraint(label="CodeArtifact", property="id", name="code_artifact_id_unique"),
    Constraint(label="TestCase", property="id", name="test_case_id_unique"),
    Constraint(label="ExternalConnection", property="id", name="external_connection_id_unique"),
    Constraint(label="Change", property="id", name="change_id_unique"),
    Constraint(
        label="ExternalResource", property="id", name="external_resource_id_unique"
    ),
    # _SchemaMigration is used by the Migrator itself; keep its key unique too.
    Constraint(
        label="_SchemaMigration", property="version", name="schema_migration_version_unique"
    ),
)

#: Constraints added in migration v2 (Phase-2 node types).
PHASE2_UNIQUENESS_CONSTRAINTS: tuple[Constraint, ...] = (
    Constraint(label="Endpoint", property="id", name="endpoint_id_unique"),
    Constraint(label="DataModel", property="id", name="data_model_id_unique"),
    Constraint(label="Query", property="id", name="query_id_unique"),
    Constraint(label="KafkaTopic", property="id", name="kafka_topic_id_unique"),
    Constraint(label="KafkaProducer", property="id", name="kafka_producer_id_unique"),
    Constraint(label="KafkaConsumer", property="id", name="kafka_consumer_id_unique"),
    Constraint(label="Mock", property="id", name="mock_id_unique"),
)

LOOKUP_INDEXES: tuple[Index, ...] = (
    # Phase-1
    Index(label="Service", property="name", name="service_name_idx"),
    Index(label="CodeArtifact", property="repo_id", name="code_artifact_repo_idx"),
    Index(label="CodeArtifact", property="type", name="code_artifact_type_idx"),
    Index(label="TestCase", property="repo_id", name="test_case_repo_idx"),
    Index(label="TestCase", property="type", name="test_case_type_idx"),
    Index(
        label="ExternalConnection",
        property="source_service_id",
        name="external_connection_source_idx",
    ),
    Index(
        label="ExternalConnection",
        property="target_service_id",
        name="external_connection_target_idx",
    ),
)

#: Indexes added in migration v2.
PHASE2_LOOKUP_INDEXES: tuple[Index, ...] = (
    Index(label="Endpoint", property="repo_id", name="endpoint_repo_idx"),
    Index(label="Endpoint", property="method", name="endpoint_method_idx"),
    Index(label="DataModel", property="repo_id", name="data_model_repo_idx"),
    Index(label="DataModel", property="kind", name="data_model_kind_idx"),
    Index(label="Query", property="repo_id", name="query_repo_idx"),
    Index(label="KafkaTopic", property="name", name="kafka_topic_name_idx"),
    Index(label="KafkaProducer", property="topic_name", name="kafka_producer_topic_idx"),
    Index(label="KafkaConsumer", property="topic_name", name="kafka_consumer_topic_idx"),
    Index(label="Mock", property="test_id", name="mock_test_idx"),
)
