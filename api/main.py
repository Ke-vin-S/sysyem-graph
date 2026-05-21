"""FastAPI application factory.

Splitting `create_app()` out from module import makes the app re-creatable
in tests (each test gets a fresh dependency-override table) and lets the
CLI entrypoint stay a one-liner."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import graph, health, pipelines, reports
from core.config import get_settings

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title="system-graph API",
        version="0.1.0",
        description=(
            "Read-only HTTP surface over the Neo4j impact graph plus "
            "adapter run-state from local SQLite stores."
        ),
    )
    # Local-only dev tool: allow the Vite dev server (default :5173) and
    # any localhost origin to call us. If you front this with a reverse
    # proxy in deployment, tighten this list.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(graph.router, prefix="/api/graph", tags=["graph"])
    app.include_router(pipelines.router, prefix="/api/pipelines", tags=["pipelines"])
    app.include_router(reports.router, prefix="/api/reports", tags=["reports"])

    return app


app = create_app()
