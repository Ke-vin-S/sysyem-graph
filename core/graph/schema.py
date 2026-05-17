"""Declarative schema: node labels, relationship types, and the indices we maintain.

Migrations consume this module. The schema is the contract between
ingestion JSON and Neo4j; if you change a label or rename a property,
write a new migration.
"""

from __future__ import annotations

from dataclasses import dataclass

NODE_LABELS: tuple[str, ...] = (
    "Service",
    "CodeArtifact",
    "TestCase",
    "ExternalConnection",
    "Change",
    "ExternalResource",
)

RELATIONSHIPS: tuple[str, ...] = (
    "CONTAINS",     # (Service)-[:CONTAINS]->(CodeArtifact)
    "DEFINES",      # (Service)-[:DEFINES]->(TestCase)
    "COVERS",       # (TestCase)-[:COVERS]->(CodeArtifact)
    "INITIATES",    # (Service)-[:INITIATES]->(ExternalConnection)
    "TARGETS",      # (ExternalConnection)-[:TARGETS]->(Service|ExternalResource)
    "EXPOSES",      # (CodeArtifact)-[:EXPOSES]->(ExternalConnection)
    "DEPENDS_ON",   # (TestCase)-[:DEPENDS_ON]->(ExternalConnection)
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

LOOKUP_INDEXES: tuple[Index, ...] = (
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
