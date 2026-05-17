"""Neo4j knowledge graph layer.

system-graph owns the Neo4j database: it defines the schema, runs migrations,
loads ingestion output idempotently, and exposes a typed query API. The DB
is derived state — wipe + reload from JSON should always work.

Entry points:
  * `Neo4jClient` — thin wrapper around neo4j-driver.
  * `Migrator` — applies versioned schema migrations.
  * `GraphLoader` — writes Service/CodeArtifact/TestCase/ExternalConnection
                    nodes + CONTAINS/DEFINES/COVERS/INITIATES/TARGETS/EXPOSES
                    edges from a MergedResult.
  * `GraphQueries` — typed read helpers (counts, lookups, traversals).
"""

from core.graph.client import Neo4jClient, Neo4jUnavailable
from core.graph.loader import GraphLoader, LoadStats
from core.graph.migrations import MIGRATIONS, Migration, Migrator
from core.graph.queries import GraphQueries
from core.graph.schema import NODE_LABELS, RELATIONSHIPS

__all__ = [
    "GraphLoader",
    "GraphQueries",
    "LoadStats",
    "MIGRATIONS",
    "Migration",
    "Migrator",
    "NODE_LABELS",
    "Neo4jClient",
    "Neo4jUnavailable",
    "RELATIONSHIPS",
]
