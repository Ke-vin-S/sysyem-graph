"""Change events and impact analysis results."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        populate_by_name=True,
    )


class ChangeType(StrEnum):
    COMMIT = "COMMIT"
    MERGE = "MERGE"
    REVERT = "REVERT"
    TAG = "TAG"


class FileOp(StrEnum):
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    REMOVED = "REMOVED"
    RENAMED = "RENAMED"


class ChangedFile(_Frozen):
    """One file touched by a Change."""

    path: str = Field(min_length=1)
    op: FileOp
    previous_path: str | None = None
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)


class Change(_Frozen):
    """A Git change event (commit, merge, etc.) that triggers impact analysis."""

    id: str = Field(min_length=1)
    """Commit SHA, or merge/PR identifier."""

    repo_id: str = Field(alias="repoId", min_length=1)
    type: ChangeType = ChangeType.COMMIT
    timestamp: datetime
    description: str = ""
    files: tuple[ChangedFile, ...] = Field(default_factory=tuple)
    affected_artifacts: tuple[str, ...] = Field(default_factory=tuple, alias="affectedArtifacts")
    """IDs of CodeArtifacts modified by this change (populated after diff analysis)."""

    author: str = ""
    parent_ids: tuple[str, ...] = Field(default_factory=tuple, alias="parentIds")


class ImpactedService(_Frozen):
    """A service identified as affected by a change, with confidence and reason."""

    service_id: str = Field(alias="serviceId", min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    depth: int = Field(ge=0)
    """Number of hops from the changed service. 0 = the changed service itself."""

    reason: str
    """Which rule produced this (e.g. 'direct_impact', 'transitive:auth->payment->order')."""

    via_connections: tuple[str, ...] = Field(default_factory=tuple, alias="viaConnections")


class ImpactAnalysisResult(_Frozen):
    """Result of running the rule engine over a single Change."""

    change_id: str = Field(alias="changeId", min_length=1)
    analyzed_at: datetime = Field(alias="analyzedAt")
    affected_services: tuple[ImpactedService, ...] = Field(
        default_factory=tuple, alias="affectedServices"
    )
    impact_chains: tuple[str, ...] = Field(default_factory=tuple, alias="impactChains")
    """Human-readable chains like 'auth->payment->order'."""

    transitive_services: tuple[str, ...] = Field(
        default_factory=tuple, alias="transitiveServices"
    )
    duration_ms: int = Field(default=0, ge=0, alias="durationMs")
    warnings: tuple[str, ...] = Field(default_factory=tuple)
