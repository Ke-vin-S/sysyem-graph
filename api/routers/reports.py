"""Report generation endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import require_neo4j
from api.schemas.reports import ImpactReportRequest, ImpactReportResponse
from api.services.graph_service import GraphService
from api.services.report_service import render_impact_report
from core.graph.client import Neo4jClient

router = APIRouter()


@router.post("/impact", response_model=ImpactReportResponse)
def impact_report(
    payload: ImpactReportRequest,
    client: Neo4jClient = Depends(require_neo4j),
) -> ImpactReportResponse:
    svc = GraphService(client)
    impact = svc.impact(payload.node_id, direction=payload.direction, depth=payload.depth)
    if impact is None:
        raise HTTPException(status_code=404, detail=f"node not found: {payload.node_id}")
    return render_impact_report(impact, title=payload.title)
