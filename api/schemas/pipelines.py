"""Pydantic schemas for the pipelines endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PipelineSummary(BaseModel):
    """One row in the pipelines dashboard.

    `id` is the stable adapter identifier (e.g. `github`, `datadog`,
    `testparser`). `enabled` reflects whether the adapter has the
    credentials/config it needs to run; `last_ran_at` is the most recent
    successful pull (ISO-8601 or empty)."""

    id: str
    label: str
    enabled: bool
    status: str = "unknown"
    """`ok`, `stale`, `error`, `disabled`, `unknown`."""
    last_ran_at: str = ""
    detail: str = ""
    """Free-form human-friendly status line for the card subtitle."""
    config: dict[str, str] = Field(default_factory=dict)
    """Sanitized config to show on the card (no secrets)."""


class GitHubRepoState(BaseModel):
    url: str
    owner: str
    name: str
    status: str
    """`registered` | `cloned` | `ingested` | `error`."""
    last_commit_sha: str = ""
    last_ingested_at: str = ""
    last_ingested_sha: str = ""
    last_error: str = ""


class GitHubPipelineDetail(PipelineSummary):
    repos: list[GitHubRepoState] = Field(default_factory=list)


class DatadogPipelineDetail(PipelineSummary):
    spans_count: int = 0
    services_count: int = 0
    spans_last_fetched_at: str = ""
    catalog_last_fetched_at: str = ""


class TestParserPipelineDetail(PipelineSummary):
    root: str = ""
    single_repo: bool | None = None
    exists: bool = False


class PipelinesResponse(BaseModel):
    pipelines: list[PipelineSummary]
