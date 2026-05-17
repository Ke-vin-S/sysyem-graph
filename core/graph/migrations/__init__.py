"""Versioned schema migrations.

Migrations are applied in order by `Migrator`. Each migration carries an
integer version and a name. Applied versions are tracked in the DB as
`_SchemaMigration` nodes so re-runs skip already-applied work.

Adding a migration: append to MIGRATIONS in the next-version slot. Never
edit an applied migration in-place; that breaks repeatability across
environments. Old migrations can be deprecated but their version numbers
are reserved forever.
"""

from core.graph.migrations.migrator import (
    MIGRATIONS,
    Migration,
    MigrationResult,
    Migrator,
)

__all__ = ["MIGRATIONS", "Migration", "MigrationResult", "Migrator"]
