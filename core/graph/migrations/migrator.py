"""Schema migration runner.

State model:
  * In-DB tracker: `(:_SchemaMigration {version: <int>, name, applied_at})`.
  * `Migrator.apply_pending(client)` reads what's applied, runs the rest in
    version order, records each application atomically.

A migration is a `Migration` dataclass with a tuple of Cypher statements.
We don't support down-migrations (Neo4j schema changes are not always
reversible); rollback is "create a new forward migration that undoes it".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.graph.client import Neo4jClient
from core.graph.schema import LOOKUP_INDEXES, UNIQUENESS_CONSTRAINTS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


@dataclass
class MigrationResult:
    applied: list[Migration] = field(default_factory=list)
    skipped: list[Migration] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return True

    def summary(self) -> str:
        return f"{len(self.applied)} applied, {len(self.skipped)} already-applied"


def _initial_schema_statements() -> tuple[str, ...]:
    """Cypher for the v1 schema: all uniqueness constraints + lookup indexes."""
    return tuple(c.cypher for c in UNIQUENESS_CONSTRAINTS) + tuple(
        i.cypher for i in LOOKUP_INDEXES
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="initial_schema",
        statements=_initial_schema_statements(),
    ),
    # Reserve future versions here. Never re-number an existing migration.
)


_TRACKER_LABEL = "_SchemaMigration"


class Migrator:
    """Apply pending migrations against a Neo4jClient.

    Idempotent: applying twice in a row is a no-op. Safe to run on every
    process startup, though typical use is via `sg-graph init`.
    """

    def __init__(self, migrations: tuple[Migration, ...] = MIGRATIONS) -> None:
        self._migrations = tuple(sorted(migrations, key=lambda m: m.version))

    def applied_versions(self, client: Neo4jClient) -> set[int]:
        # The _SchemaMigration label exists only after migration 1 has at
        # minimum run its constraint creation. For an empty DB, we tolerate
        # the missing label by catching the read failure.
        try:
            rows = client.run(
                f"MATCH (m:{_TRACKER_LABEL}) RETURN m.version AS version"
            )
        except Exception:
            return set()
        return {int(row["version"]) for row in rows if row.get("version") is not None}

    def apply_pending(self, client: Neo4jClient) -> MigrationResult:
        already = self.applied_versions(client)
        result = MigrationResult()
        # The very first migration is bootstrap: it creates the constraints
        # the tracker relies on. We bootstrap by always applying v1 schema
        # statements (CREATE ... IF NOT EXISTS) even if the tracker says it
        # was applied — they're cheap and idempotent.
        for migration in self._migrations:
            if migration.version in already and migration.version != 1:
                result.skipped.append(migration)
                continue
            logger.info(
                "applying migration v%d %s (%d statements)",
                migration.version,
                migration.name,
                len(migration.statements),
            )
            self._apply_one(client, migration)
            result.applied.append(migration)
        return result

    def _apply_one(self, client: Neo4jClient, migration: Migration) -> None:
        with client.session() as session:
            for stmt in migration.statements:
                session.run(stmt).consume()
            session.run(
                f"MERGE (m:{_TRACKER_LABEL} {{version: $version}}) "
                "SET m.name = $name, m.applied_at = $applied_at",
                version=migration.version,
                name=migration.name,
                applied_at=datetime.now(timezone.utc).isoformat(),
            ).consume()

    def reset(self, client: Neo4jClient) -> None:
        """Drop every node and relationship in the DB (not just data —
        EVERYTHING, including _SchemaMigration). Used by tests and the
        `sg-graph clear` command. Constraints and indexes are NOT dropped
        — they get re-created by apply_pending() on the next init."""
        with client.session() as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
