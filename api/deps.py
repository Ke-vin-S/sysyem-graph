"""FastAPI dependencies.

We instantiate Neo4j and SQLite-store clients per-request. They're cheap
to construct (Neo4j driver is lazy; SQLite is file-open). Each request
runs in its own session and closes everything on the way out. This keeps
the API stateless and re-startable without any cleanup ceremony.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, status

from core.config import get_settings
from core.graph.client import Neo4jClient, Neo4jUnavailable
from core.graph.queries import GraphQueries


def neo4j_client() -> Iterator[Neo4jClient]:
    client = Neo4jClient()
    try:
        yield client
    finally:
        client.close()


def graph_queries(
    client: Neo4jClient = Depends(neo4j_client),
) -> GraphQueries:
    return GraphQueries(client)


def require_neo4j(
    client: Neo4jClient = Depends(neo4j_client),
) -> Neo4jClient:
    """Use on endpoints that hard-require a live Neo4j connection.

    The healthcheck is cheap (`RETURN 1`) so per-request gating is OK.
    503 surfaces the "Neo4j down" case as something the UI can render
    instead of returning a confusing 500."""
    if not client.healthcheck():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"neo4j not reachable at {client.uri}",
        )
    return client


def settings_dep():  # type: ignore[no-untyped-def]
    return get_settings()


__all__ = [
    "graph_queries",
    "neo4j_client",
    "Neo4jUnavailable",
    "require_neo4j",
    "settings_dep",
]
