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
    services = list(report.merged.services.values())
    connections = list(report.merged.connections.values())
    artifacts = list(report.merged.artifacts.values())
    tests = list(report.merged.tests.values())
    _dump(out / "services.json", [s.model_dump(mode="json") for s in services])
    _dump(out / "connections.json", [c.model_dump(mode="json") for c in connections])
    _dump(out / "artifacts.json", [a.model_dump(mode="json") for a in artifacts])
    _dump(out / "tests.json", [t.model_dump(mode="json") for t in tests])
    _dump(out / "relationships.json", _build_relationships(services, connections, artifacts, tests))
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


def _build_relationships(services, connections, artifacts, tests) -> list[dict[str, Any]]:
    """Materialize the Neo4j edge list from foreign keys on the records.

    Edges emitted (matching the Neo4j schema in docs/QUICK_REFERENCE.md):
      (Service)-[:CONTAINS]->(CodeArtifact)
      (Service)-[:DEFINES]->(TestCase)
      (TestCase)-[:COVERS]->(CodeArtifact)
      (Service)-[:INITIATES]->(ExternalConnection)
      (ExternalConnection)-[:TARGETS]->(Service)        when target_service_id known
      (CodeArtifact)-[:EXPOSES]->(ExternalConnection)    from artifact.external_connections
    """
    service_ids = {s.id for s in services}
    edges: list[dict[str, Any]] = []

    for artifact in artifacts:
        if artifact.repo_id in service_ids:
            edges.append({"src": artifact.repo_id, "rel": "CONTAINS", "dst": artifact.id})
        for conn_id in artifact.external_connections:
            edges.append({"src": artifact.id, "rel": "EXPOSES", "dst": conn_id})

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
