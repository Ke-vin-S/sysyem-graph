"""Datadog APM trace ingestion adapter."""

from ingestion.adapters.datadog.adapter import DatadogAdapter
from ingestion.adapters.datadog.client import (
    DatadogClient,
    RawServiceDefinition,
    RawSpan,
)
from ingestion.adapters.datadog.config import DatadogAdapterConfig
from ingestion.adapters.datadog.store import DatadogStore, FetchRecord
from ingestion.adapters.datadog.trace_parser import TraceParser

__all__ = [
    "DatadogAdapter",
    "DatadogAdapterConfig",
    "DatadogClient",
    "DatadogStore",
    "FetchRecord",
    "RawServiceDefinition",
    "RawSpan",
    "TraceParser",
]
