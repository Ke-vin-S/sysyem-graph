"""DatadogFetcher: pull from Datadog APIs, write to DatadogStore.

Strict separation from the parser: fetcher writes only, never returns
domain objects to callers. Re-fetches are idempotent thanks to the
store's natural keys.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from core.types.errors import IngestionError
from ingestion.adapters.datadog.client import DatadogClient
from ingestion.adapters.datadog.store import DatadogStore

logger = logging.getLogger(__name__)


class DatadogFetcher:
    """Bridges the network-bound Datadog client and the on-disk store.

    Every method records a `fetch_log` audit row on the way out, success
    or failure — failures leave a tracking row that tells the next run to
    retry instead of trusting a stale success.
    """

    def __init__(self, store: DatadogStore, client: DatadogClient) -> None:
        self._store = store
        self._client = client

    def fetch_spans(
        self,
        *,
        lookback_hours: int,
        query: str = "*",
        env: str = "",
        page_limit: int = 1000,
        max_pages: int = 100,
    ) -> int:
        """Pull spans for the given window into the store.

        Returns the count of spans written. Raises `IngestionError` on
        unrecoverable failure (after recording the failure in fetch_log).
        """
        t0 = time.perf_counter()
        fetched_at = datetime.now(timezone.utc)
        logger.info(
            "datadog_fetcher: fetching spans lookback=%dh query=%r env=%r",
            lookback_hours,
            query,
            env or "<all>",
        )
        try:
            spans_iter = self._client.list_spans(
                lookback_hours=lookback_hours,
                query=query,
                page_limit=page_limit,
                max_pages=max_pages,
            )
            count = self._store.insert_spans(spans_iter, env=env, fetched_at=fetched_at)
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            self._store.record_fetch(
                api="spans",
                rows_written=0,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
                fetched_at=fetched_at,
            )
            if isinstance(exc, IngestionError):
                raise
            raise IngestionError("datadog", "span fetch failed", cause=exc) from exc

        duration_ms = (time.perf_counter() - t0) * 1000
        self._store.record_fetch(
            api="spans",
            rows_written=count,
            status="success",
            duration_ms=duration_ms,
            fetched_at=fetched_at,
        )
        logger.info("datadog_fetcher: wrote %d spans in %.0fms", count, duration_ms)
        return count

    def fetch_catalog(self, *, schema_version: str = "v2.2") -> int:
        """Pull the full Service Catalog into the store. Returns the count
        of definitions written.

        The catalog is small (one row per service, usually tens to
        hundreds total) so we always upsert the whole list — no
        pagination, no incremental sync. The TTL controls how often we
        do this (default 1h, set via per-API config).
        """
        t0 = time.perf_counter()
        fetched_at = datetime.now(timezone.utc)
        logger.info("datadog_fetcher: fetching service catalog (schema=%s)", schema_version)
        try:
            defs_iter = self._client.list_service_definitions(schema_version=schema_version)
            count = self._store.insert_service_definitions(defs_iter, fetched_at=fetched_at)
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            self._store.record_fetch(
                api="catalog",
                rows_written=0,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
                fetched_at=fetched_at,
            )
            if isinstance(exc, IngestionError):
                raise
            raise IngestionError("datadog", "service catalog fetch failed", cause=exc) from exc

        duration_ms = (time.perf_counter() - t0) * 1000
        self._store.record_fetch(
            api="catalog",
            rows_written=count,
            status="success",
            duration_ms=duration_ms,
            fetched_at=fetched_at,
        )
        logger.info("datadog_fetcher: wrote %d catalog entries in %.0fms", count, duration_ms)
        return count
