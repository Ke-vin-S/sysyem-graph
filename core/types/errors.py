"""Custom exception hierarchy for system-graph."""

from __future__ import annotations


class SystemGraphError(Exception):
    """Base class for all system-graph errors."""


class ConfigurationError(SystemGraphError):
    """Raised when configuration is missing or invalid."""


class IngestionError(SystemGraphError):
    """Raised when an ingestion adapter fails to extract data."""

    def __init__(self, adapter: str, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(f"[{adapter}] {message}")
        self.adapter = adapter
        self.cause = cause


class AdapterError(SystemGraphError):
    """Raised when an adapter encounters an internal error (bad response, parse failure)."""


class ValidationError(SystemGraphError):
    """Raised when ingested data fails validation (dangling refs, schema mismatch)."""
