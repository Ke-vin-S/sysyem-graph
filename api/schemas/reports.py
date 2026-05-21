"""Pydantic schemas for the reports endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImpactReportRequest(BaseModel):
    node_id: str
    direction: str = "downstream"
    depth: int = Field(default=3, ge=1, le=10)
    title: str = ""


class ImpactReportResponse(BaseModel):
    """Generated report — plain markdown text the UI displays AND lets
    the user download."""

    title: str
    markdown: str
    generated_at: str
    node_count: int
