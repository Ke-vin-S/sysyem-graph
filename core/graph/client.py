"""Neo4jClient — thin context-manager wrapper around neo4j-driver.

Why a wrapper at all:

  * Centralized session/transaction lifecycle so callers don't have to track
    driver vs session vs transaction context.
  * Lazy driver creation (so importing this module doesn't open a connection).
  * Friendly Neo4jUnavailable exception for tests that need to skip when the
    DB isn't running.
  * Single place to surface "we are the source of truth" invariants: every
    query goes through a session bound to the configured database name.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from core.config import Neo4jSettings
from core.types.errors import SystemGraphError

logger = logging.getLogger(__name__)


class Neo4jUnavailable(SystemGraphError):
    """Raised when the configured Neo4j is unreachable. Integration tests
    catch this to skip gracefully."""


class Neo4jClient:
    """Lazy Neo4j connection. Use as a context manager or call close() manually."""

    def __init__(self, settings: Neo4jSettings | None = None) -> None:
        self._settings = settings or Neo4jSettings()
        self._driver: Any | None = None

    @property
    def database(self) -> str:
        return self._settings.database

    @property
    def uri(self) -> str:
        return self._settings.uri

    def driver(self) -> Any:
        """Return the underlying driver, creating it on first use."""
        if self._driver is not None:
            return self._driver
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:  # pragma: no cover
            raise Neo4jUnavailable(f"neo4j package not installed: {exc}") from exc
        password = self._settings.password.get_secret_value()
        try:
            self._driver = GraphDatabase.driver(
                self._settings.uri,
                auth=(self._settings.user, password),
            )
        except Exception as exc:  # pragma: no cover - network/auth errors
            raise Neo4jUnavailable(f"failed to open driver: {exc}") from exc
        return self._driver

    def healthcheck(self) -> bool:
        """Run a no-op query to confirm the DB is reachable. Returns False
        rather than raising — integration tests use it to skip."""
        try:
            with self.session() as session:
                session.run("RETURN 1 AS ok").consume()
        except Exception as exc:
            logger.debug("Neo4j healthcheck failed: %s", exc)
            return False
        return True

    @contextmanager
    def session(self) -> Iterator[Any]:
        driver = self.driver()
        session = driver.session(database=self._settings.database)
        try:
            yield session
        finally:
            session.close()

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """One-shot helper for read-ish queries.

        Returns plain dicts so call sites don't leak driver types. For
        anything inside a larger transaction, open a session() block directly.
        """
        with self.session() as session:
            result = session.run(cypher, **params)
            return [record.data() for record in result]

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:  # pragma: no cover
                pass
            self._driver = None

    def __enter__(self) -> "Neo4jClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
