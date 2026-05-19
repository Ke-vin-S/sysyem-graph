"""Datadog adapter configuration."""

from __future__ import annotations

from dataclasses import dataclass

from core.config import DatadogSettings


@dataclass
class DatadogAdapterConfig:
    """Per-run knobs for the Datadog adapter, derived from env settings.

    Kept separate from the env-loaded `DatadogSettings` so callers (tests,
    notebooks) can construct one inline without setting env vars.
    """

    api_key: str
    app_key: str
    site: str = "datadoghq.com"
    lookback_hours: int = 720
    min_span_count: int = 1
    """A (source, target, endpoint) tuple must appear at least this many times to
    be recorded as an ExternalConnection. Filters out one-off curl calls."""

    services_allowlist: tuple[str, ...] = ()
    """If non-empty, only ingest spans where source service is in this list."""

    env: str = ""
    """Optional `env:<value>` Datadog tag filter (e.g. 'prod'). Empty = no filter.
    Keeps staging/dev traffic out of the production impact graph."""

    spans_ttl_seconds: int = 300
    """How long a `spans` fetch stays "fresh" — re-running `extract` within
    this window reuses the staged spans instead of hitting the Datadog API
    again. Default 5 minutes; bump higher to cache for the whole working
    session, lower to fetch every run."""

    catalog_ttl_seconds: int = 3600
    """How long the Service Catalog stays fresh. Definitions change rarely
    so the default is 1 hour — orders of magnitude longer than spans."""

    store_path: str = "./out/datadog.db"
    """On-disk SQLite path for the staging store. `:memory:` for tests."""

    @classmethod
    def from_settings(cls, settings: DatadogSettings) -> "DatadogAdapterConfig":
        if not settings.enabled:
            raise ValueError("Datadog API/APP keys are not configured")
        assert settings.api_key is not None and settings.app_key is not None
        return cls(
            api_key=settings.api_key.get_secret_value(),
            app_key=settings.app_key.get_secret_value(),
            site=settings.site,
            lookback_hours=settings.trace_lookback_hours,
            env=settings.env,
            spans_ttl_seconds=settings.spans_ttl_seconds,
            catalog_ttl_seconds=settings.catalog_ttl_seconds,
            store_path=settings.store_path,
        )
