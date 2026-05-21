"""Graph endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import neo4j_client, require_neo4j
from api.schemas.graph import (
    GraphCountsResponse,
    GraphNode,
    GraphSubgraph,
    ImpactResponse,
    SearchHit,
)
from api.services.graph_service import GraphService
from core.graph.client import Neo4jClient
from core.graph.queries import GraphQueries

router = APIRouter()


def _service(client: Neo4jClient = Depends(require_neo4j)) -> GraphService:
    return GraphService(client)


@router.get("/counts", response_model=GraphCountsResponse)
def counts(client: Neo4jClient = Depends(neo4j_client)) -> GraphCountsResponse:
    """Top-level node and edge counts. Light enough to call on every
    page load to show a graph-size badge."""
    if not client.healthcheck():
        return GraphCountsResponse(
            services=0, artifacts=0, tests=0, connections=0, edges={}
        )
    counts = GraphQueries(client).counts()
    return GraphCountsResponse(
        services=counts.services,
        artifacts=counts.artifacts,
        tests=counts.tests,
        connections=counts.connections,
        edges=counts.edges,
    )


@router.get("/services", response_model=list[GraphNode])
def services(svc: GraphService = Depends(_service)) -> list[GraphNode]:
    return svc.list_services()


@router.get("/overview", response_model=GraphSubgraph)
def overview(svc: GraphService = Depends(_service)) -> GraphSubgraph:
    """Services + service-to-service edges. The default Explorer view."""
    return svc.service_overview()


@router.get("/nodes/{node_id}", response_model=GraphNode)
def node(node_id: str, svc: GraphService = Depends(_service)) -> GraphNode:
    n = svc.get_node(node_id)
    if n is None:
        raise HTTPException(status_code=404, detail=f"node not found: {node_id}")
    return n


@router.get("/nodes/{node_id}/neighborhood", response_model=GraphSubgraph)
def neighborhood(
    node_id: str,
    depth: int = Query(default=1, ge=1, le=3),
    svc: GraphService = Depends(_service),
) -> GraphSubgraph:
    sub = svc.neighborhood(node_id, depth=depth)
    if not sub.nodes:
        raise HTTPException(status_code=404, detail=f"node not found: {node_id}")
    return sub


@router.get("/nodes/{node_id}/impact", response_model=ImpactResponse)
def impact(
    node_id: str,
    direction: str = Query(default="downstream", pattern="^(downstream|upstream)$"),
    depth: int = Query(default=3, ge=1, le=8),
    svc: GraphService = Depends(_service),
) -> ImpactResponse:
    result = svc.impact(node_id, direction=direction, depth=depth)
    if result is None:
        raise HTTPException(status_code=404, detail=f"node not found: {node_id}")
    return result


@router.get("/search", response_model=list[SearchHit])
def search(
    q: str = Query(min_length=1),
    limit: int = Query(default=25, ge=1, le=200),
    svc: GraphService = Depends(_service),
) -> list[SearchHit]:
    return svc.search(q, limit=limit)
