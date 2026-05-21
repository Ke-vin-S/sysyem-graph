"""Pipeline endpoints — read-only views over adapter state."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import settings_dep
from api.schemas.pipelines import (
    DatadogPipelineDetail,
    GitHubPipelineDetail,
    PipelinesResponse,
    PipelineSummary,
    TestParserPipelineDetail,
)
from api.services.pipeline_service import PipelineService

router = APIRouter()


def _service(settings=Depends(settings_dep)) -> PipelineService:  # type: ignore[no-untyped-def]
    return PipelineService(settings)


@router.get("", response_model=PipelinesResponse)
def list_pipelines(svc: PipelineService = Depends(_service)) -> PipelinesResponse:
    return PipelinesResponse(pipelines=svc.list_pipelines())


@router.get("/github", response_model=GitHubPipelineDetail)
def github(svc: PipelineService = Depends(_service)) -> GitHubPipelineDetail:
    return svc.github_detail()


@router.get("/datadog", response_model=DatadogPipelineDetail)
def datadog(svc: PipelineService = Depends(_service)) -> DatadogPipelineDetail:
    return svc.datadog_detail()


@router.get("/testparser", response_model=TestParserPipelineDetail)
def testparser(svc: PipelineService = Depends(_service)) -> TestParserPipelineDetail:
    return svc.testparser_detail()


@router.get("/{pipeline_id}", response_model=PipelineSummary)
def get_pipeline(pipeline_id: str, svc: PipelineService = Depends(_service)) -> PipelineSummary:
    """Fallback for arbitrary IDs — returns the same summary the
    dashboard renders. Useful when the UI deep-links to a specific
    pipeline before knowing its detail shape."""
    summaries = {p.id: p for p in svc.list_pipelines()}
    if pipeline_id not in summaries:
        raise HTTPException(status_code=404, detail=f"unknown pipeline: {pipeline_id}")
    return summaries[pipeline_id]
