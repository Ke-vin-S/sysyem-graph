"""Pydantic v2 domain model for the impact analysis graph."""

from core.types.change import Change, ChangedFile, ImpactAnalysisResult, ImpactedService
from core.types.errors import (
    AdapterError,
    ConfigurationError,
    IngestionError,
    SystemGraphError,
    ValidationError,
)
from core.types.service import (
    Criticality,
    CodeArtifact,
    ContractStatus,
    Direction,
    ExternalConnection,
    LineRange,
    Service,
    TestCase,
    TestType,
)

__all__ = [
    "AdapterError",
    "Change",
    "ChangedFile",
    "CodeArtifact",
    "ConfigurationError",
    "ContractStatus",
    "Criticality",
    "Direction",
    "ExternalConnection",
    "ImpactAnalysisResult",
    "ImpactedService",
    "IngestionError",
    "LineRange",
    "Service",
    "SystemGraphError",
    "TestCase",
    "TestType",
    "ValidationError",
]
