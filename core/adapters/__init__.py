"""Ingestion adapter framework: base class, registry, merger, mapper, validator."""

from core.adapters.base import (
    AdapterResult,
    Coverage,
    IngestionAdapter,
    IngestionContext,
)
from core.adapters.confidence_scorer import ConfidenceScorer
from core.adapters.mapper import ArtifactConnectionMapper
from core.adapters.merger import ResultMerger
from core.adapters.registry import AdapterRegistry, RegisteredAdapter
from core.adapters.validator import ResultValidator

__all__ = [
    "AdapterRegistry",
    "AdapterResult",
    "ArtifactConnectionMapper",
    "ConfidenceScorer",
    "Coverage",
    "IngestionAdapter",
    "IngestionContext",
    "RegisteredAdapter",
    "ResultMerger",
    "ResultValidator",
]
