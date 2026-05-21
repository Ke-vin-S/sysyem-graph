"""Pipeline-state service.

Aggregates the last-run metadata each adapter records in its own SQLite
store into a single dashboard view. No writes here — the UI is a
read-only window onto state owned by the ingestion CLI.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.schemas.pipelines import (
    DatadogPipelineDetail,
    GitHubPipelineDetail,
    GitHubRepoState,
    PipelineSummary,
    TestParserPipelineDetail,
)
from core.config import Settings

logger = logging.getLogger(__name__)


_STALE_SECONDS = 24 * 60 * 60


def _staleness(at_iso: str) -> str:
    """Bucket an ISO timestamp into `ok` / `stale` / `unknown`."""
    if not at_iso:
        return "unknown"
    try:
        dt = datetime.fromisoformat(at_iso)
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - dt).total_seconds()
    return "ok" if age < _STALE_SECONDS else "stale"


class PipelineService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ---- summary endpoint --------------------------------------------

    def list_pipelines(self) -> list[PipelineSummary]:
        return [
            self.github_detail(),
            self.datadog_detail(),
            self.testparser_detail(),
        ]

    # ---- per-adapter detail ------------------------------------------

    def github_detail(self) -> GitHubPipelineDetail:
        cfg = self._settings.github
        enabled = True  # public repos work without a token; surface the state regardless
        repos: list[GitHubRepoState] = []
        last_at = ""
        store_path = cfg.store_path
        if Path(store_path).exists() if store_path != ":memory:" else False:
            try:
                from ingestion.adapters.github.store import GitHubStore

                with GitHubStore(store_path) as store:
                    for record in store.list_repos():
                        repos.append(
                            GitHubRepoState(
                                url=record.url,
                                owner=record.owner,
                                name=record.name,
                                status=record.status,
                                last_commit_sha=record.last_commit_sha,
                                last_ingested_at=record.last_ingested_at,
                                last_ingested_sha=record.last_ingested_sha,
                                last_error=record.last_error,
                            )
                        )
                        if record.last_ingested_at > last_at:
                            last_at = record.last_ingested_at
            except Exception as exc:  # pragma: no cover - corrupted store
                logger.warning("github store read failed: %s", exc)
        status = "ok" if last_at else ("disabled" if not enabled else "unknown")
        if last_at:
            status = _staleness(last_at)
        detail = (
            f"{len(repos)} repo(s) registered"
            + (f", last ingest {last_at}" if last_at else "")
        )
        return GitHubPipelineDetail(
            id="github",
            label="GitHub",
            enabled=enabled,
            status=status,
            last_ran_at=last_at,
            detail=detail,
            config={
                "api_url": cfg.api_url,
                "clones_dir": str(cfg.clones_dir),
                "store_path": store_path,
                "token_configured": "yes" if cfg.token else "no",
                "seed_repos": ",".join(cfg.repos) if cfg.repos else "(via store)",
            },
            repos=repos,
        )

    def datadog_detail(self) -> DatadogPipelineDetail:
        cfg = self._settings.datadog
        enabled = cfg.enabled
        store_path = cfg.store_path
        spans_count = 0
        services_count = 0
        spans_last = ""
        catalog_last = ""
        if Path(store_path).exists():
            try:
                from ingestion.adapters.datadog.store import DatadogStore

                store = DatadogStore(store_path)
                try:
                    spans_count = store.span_count()
                    services_count = store.service_definition_count()
                    last_spans = store.last_fetched_at("spans")
                    last_catalog = store.last_fetched_at("catalog")
                    if last_spans:
                        spans_last = last_spans.isoformat()
                    if last_catalog:
                        catalog_last = last_catalog.isoformat()
                finally:
                    store.close()
            except Exception as exc:  # pragma: no cover
                logger.warning("datadog store read failed: %s", exc)
        last_at = max(spans_last, catalog_last)
        if not enabled:
            status = "disabled"
        elif last_at:
            status = _staleness(last_at)
        else:
            status = "unknown"
        detail = (
            f"spans={spans_count}, services={services_count}"
            + (f", last fetch {last_at}" if last_at else "")
        )
        return DatadogPipelineDetail(
            id="datadog",
            label="Datadog APM",
            enabled=enabled,
            status=status,
            last_ran_at=last_at,
            detail=detail,
            config={
                "site": cfg.site,
                "env": cfg.env or "(all)",
                "lookback_hours": str(cfg.trace_lookback_hours),
                "store_path": store_path,
                "api_key_set": "yes" if cfg.api_key else "no",
                "app_key_set": "yes" if cfg.app_key else "no",
            },
            spans_count=spans_count,
            services_count=services_count,
            spans_last_fetched_at=spans_last,
            catalog_last_fetched_at=catalog_last,
        )

    def testparser_detail(self) -> TestParserPipelineDetail:
        cfg = self._settings.testparser
        root = Path(cfg.root)
        exists = root.exists()
        status = "ok" if exists else "unknown"
        detail = f"root: {root}" + ("" if exists else " (missing)")
        return TestParserPipelineDetail(
            id="testparser",
            label="Filesystem (TestParser)",
            enabled=True,
            status=status,
            last_ran_at="",
            detail=detail,
            config={
                "root": str(root),
                "single_repo": str(cfg.single_repo) if cfg.single_repo is not None else "auto",
            },
            root=str(root),
            single_repo=cfg.single_repo,
            exists=exists,
        )


__all__ = ["PipelineService"]


# Sub-detail typed-dict helpers: kept here so the routers can show
# adapter-specific fields without an `isinstance(..., GitHubPipelineDetail)`
# dance.
def detail_as_dict(detail: Any) -> dict[str, Any]:
    return detail.model_dump() if hasattr(detail, "model_dump") else dict(detail)
