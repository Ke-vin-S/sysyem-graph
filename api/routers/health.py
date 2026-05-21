"""Health endpoints — used by the UI to render a connection banner.

`/health` is unconditionally 200 once the API process is up.
`/health/neo4j` reports the DB connection state without raising.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import neo4j_client
from core.graph.client import Neo4jClient

router = APIRouter()


@router.get("/health")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/neo4j")
def neo4j_health(
    client: Neo4jClient = Depends(neo4j_client),
) -> dict[str, object]:
    reachable = client.healthcheck()
    return {"reachable": reachable, "uri": client.uri, "database": client.database}
