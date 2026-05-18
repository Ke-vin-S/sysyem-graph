"""Typer CLI for running Phase 1 ingestion.

Phase 1 stops at producing in-memory + JSON-on-disk records; the Neo4j
loader is Phase 2 work. For now `sg-ingest run --out ./out` dumps merged
JSON files that the loader will later consume.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import typer

from core.adapters import AdapterRegistry, IngestionContext
from core.config import get_settings
from core.types.errors import ConfigurationError
from ingestion.adapters.datadog import DatadogAdapter, DatadogAdapterConfig
from ingestion.adapters.github import GitHubAdapter, GitHubAdapterConfig
from ingestion.adapters.testparser import TestParserAdapter, TestParserAdapterConfig

app = typer.Typer(help="system-graph ingestion CLI")
logger = logging.getLogger(__name__)


@app.command()
def run(
    out: Path = typer.Option(Path("./out"), help="Directory to write merged records as JSON."),
    skip: list[str] = typer.Option(  # noqa: B008
        [],
        "--skip",
        help="Adapter identifier(s) to disable for this run (e.g. --skip datadog).",
    ),
) -> None:
    """Run all configured Phase 1 adapters and write merged records to disk."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    registry = AdapterRegistry()
    _register_adapters(registry, settings, skipped=set(skip))

    if not registry.list_adapters():
        raise ConfigurationError(
            "no adapters configured. Set DD_API_KEY/DD_APP_KEY, GITHUB_TOKEN, "
            "or TESTPARSER_ROOT in your environment (.env)."
        )

    report = registry.run_all(IngestionContext())
    typer.echo(f"adapters ok: {sorted(registry._adapters)}")  # noqa: SLF001
    typer.echo(f"counts: {report.merged.counts()}")
    if report.failures:
        typer.secho(f"failures: {report.failures}", fg=typer.colors.RED)
    for warning in report.validation.warnings:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW)
    for error in report.validation.errors:
        typer.secho(f"error: {error}", fg=typer.colors.RED)

    out.mkdir(parents=True, exist_ok=True)
    merged = report.merged
    services = list(merged.services.values())
    connections = list(merged.connections.values())
    artifacts = list(merged.artifacts.values())
    tests = list(merged.tests.values())
    endpoints = list(merged.endpoints.values())
    data_models = list(merged.data_models.values())
    queries = list(merged.queries.values())
    kafka_topics = list(merged.kafka_topics.values())
    kafka_producers = list(merged.kafka_producers.values())
    kafka_consumers = list(merged.kafka_consumers.values())
    mocks = list(merged.mocks.values())

    _dump(out / "services.json", [s.model_dump(mode="json") for s in services])
    _dump(out / "connections.json", [c.model_dump(mode="json") for c in connections])
    _dump(out / "artifacts.json", [a.model_dump(mode="json") for a in artifacts])
    _dump(out / "tests.json", [t.model_dump(mode="json") for t in tests])
    _dump(out / "endpoints.json", [e.model_dump(mode="json") for e in endpoints])
    _dump(out / "data_models.json", [d.model_dump(mode="json") for d in data_models])
    _dump(out / "queries.json", [q.model_dump(mode="json") for q in queries])
    _dump(out / "kafka_topics.json", [t.model_dump(mode="json") for t in kafka_topics])
    _dump(out / "kafka_producers.json", [p.model_dump(mode="json") for p in kafka_producers])
    _dump(out / "kafka_consumers.json", [c.model_dump(mode="json") for c in kafka_consumers])
    _dump(out / "mocks.json", [m.model_dump(mode="json") for m in mocks])
    _dump(
        out / "suggestions.json",
        [s.model_dump(mode="json") for s in merged.suggestions.values()],
    )
    _dump(
        out / "relationships.json",
        _build_relationships(
            services,
            connections,
            artifacts,
            tests,
            endpoints,
            data_models,
            queries,
            kafka_producers,
            kafka_consumers,
            mocks,
        ),
    )
    typer.echo(f"wrote merged records to {out}")

    if report.failures or report.validation.errors:
        raise typer.Exit(code=1)


def _register_adapters(
    registry: AdapterRegistry, settings: Any, skipped: set[str]
) -> None:
    if "datadog" not in skipped and settings.datadog.enabled:
        cfg = DatadogAdapterConfig.from_settings(settings.datadog)
        registry.register(DatadogAdapter(cfg))
    if "github" not in skipped and settings.github.enabled and settings.github.repos:
        cfg = GitHubAdapterConfig.from_settings(settings.github)
        registry.register(GitHubAdapter(cfg))
    if "testparser" not in skipped:
        cfg = TestParserAdapterConfig.from_settings(settings.testparser)
        if cfg.root.exists():
            registry.register(TestParserAdapter(cfg))


def _build_relationships(
    services,
    connections,
    artifacts,
    tests,
    endpoints,
    data_models,
    queries,
    kafka_producers,
    kafka_consumers,
    mocks,
) -> list[dict[str, Any]]:
    """Materialize the Neo4j edge list from foreign keys on the records.

    Mirrors what GraphLoader writes — kept here so the JSON dump is
    self-describing for downstream consumers that don't replay through the
    loader.
    """
    service_ids = {s.id for s in services}
    edges: list[dict[str, Any]] = []

    for artifact in artifacts:
        if artifact.repo_id in service_ids:
            edges.append({"src": artifact.repo_id, "rel": "CONTAINS", "dst": artifact.id})
        for conn_id in artifact.external_connections:
            edges.append({"src": artifact.id, "rel": "EXPOSES", "dst": conn_id})
        for callee_id in artifact.calls:
            edges.append({"src": artifact.id, "rel": "CALLS", "dst": callee_id})

    for test in tests:
        if test.repo_id in service_ids:
            edges.append({"src": test.repo_id, "rel": "DEFINES", "dst": test.id})
        for artifact_id in test.covers_artifacts:
            edges.append({"src": test.id, "rel": "COVERS", "dst": artifact_id})

    for conn in connections:
        if conn.source_service_id in service_ids:
            edges.append({"src": conn.source_service_id, "rel": "INITIATES", "dst": conn.id})
        if conn.target_service_id and conn.target_service_id in service_ids:
            edges.append({"src": conn.id, "rel": "TARGETS", "dst": conn.target_service_id})

    for endpoint in endpoints:
        if endpoint.repo_id in service_ids:
            edges.append({"src": endpoint.repo_id, "rel": "CONTAINS", "dst": endpoint.id})
        if endpoint.handler_artifact_id:
            edges.append(
                {"src": endpoint.id, "rel": "HANDLED_BY", "dst": endpoint.handler_artifact_id}
            )

    for dm in data_models:
        if dm.repo_id in service_ids:
            edges.append({"src": dm.repo_id, "rel": "CONTAINS", "dst": dm.id})

    for query in queries:
        if query.repo_id in service_ids:
            edges.append({"src": query.repo_id, "rel": "CONTAINS", "dst": query.id})
        if query.enclosing_artifact_id:
            edges.append(
                {"src": query.enclosing_artifact_id, "rel": "EXECUTES", "dst": query.id}
            )

    for producer in kafka_producers:
        edges.append(
            {"src": producer.id, "rel": "PRODUCES", "dst": f"topic:{producer.topic_name}"}
        )

    for consumer in kafka_consumers:
        edges.append(
            {"src": consumer.id, "rel": "CONSUMES", "dst": f"topic:{consumer.topic_name}"}
        )

    for mock in mocks:
        if mock.target_artifact_id:
            edges.append(
                {"src": mock.test_id, "rel": "MOCKS", "dst": mock.target_artifact_id}
            )

    return edges


def _dump(path: Path, payload: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(payload, indent=2, default=_default), encoding="utf-8")


def _default(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
