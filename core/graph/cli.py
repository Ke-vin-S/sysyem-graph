"""sg-graph CLI: init / load / status / clear / query."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import typer

from core.adapters.merger import MergedResult
from core.config import get_settings
from core.graph.client import Neo4jClient, Neo4jUnavailable
from core.graph.loader import GraphLoader
from core.graph.migrations import Migrator
from core.graph.queries import GraphQueries
from core.types import (
    CodeArtifact,
    DataModel,
    Endpoint,
    ExternalConnection,
    KafkaConsumer,
    KafkaProducer,
    KafkaTopic,
    Mock,
    Query,
    Service,
    Suggestion,
    TestCase,
)

app = typer.Typer(help="system-graph Neo4j control plane")
logger = logging.getLogger(__name__)


def _quiet_neo4j_notifications() -> None:
    """Silence the driver's INFO-level "constraint already exists" notices
    that fire on every idempotent migration run. Cosmetic only."""
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


@app.command()
def init() -> None:
    """Apply pending schema migrations. Idempotent; safe to run repeatedly."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    _quiet_neo4j_notifications()
    with Neo4jClient(settings.neo4j) as client:
        _require_neo4j(client)
        result = Migrator().apply_pending(client)
        typer.echo(result.summary())
        for m in result.applied:
            typer.echo(f"  applied v{m.version} {m.name}")


@app.command()
def load(
    from_: Path = typer.Option(  # noqa: B008
        Path("./out"),
        "--from",
        help="Directory containing services.json/artifacts.json/tests.json/connections.json.",
    ),
) -> None:
    """Load ingestion output (JSON files) into Neo4j with MERGE semantics."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    _quiet_neo4j_notifications()

    merged = _read_merged(from_)
    with Neo4jClient(settings.neo4j) as client:
        _require_neo4j(client)
        # Ensure schema is in place; load is a no-op without constraints.
        Migrator().apply_pending(client)
        stats = GraphLoader(client).load(merged)
    typer.echo(f"loaded: {stats.counts()}")


@app.command()
def status() -> None:
    """Print node and edge counts."""
    settings = get_settings()
    with Neo4jClient(settings.neo4j) as client:
        _require_neo4j(client)
        counts = GraphQueries(client).counts()
    typer.echo(
        "nodes: "
        f"Service={counts.services} "
        f"CodeArtifact={counts.artifacts} "
        f"TestCase={counts.tests} "
        f"ExternalConnection={counts.connections}"
    )
    typer.echo("edges: " + " ".join(f"{k}={v}" for k, v in counts.edges.items()))


@app.command()
def clear(
    confirm: bool = typer.Option(
        False, "--yes", help="Required to actually wipe the graph."
    ),
) -> None:
    """Wipe ALL nodes and relationships. Constraints/indexes are preserved."""
    if not confirm:
        typer.echo("refusing without --yes (would delete every node)")
        raise typer.Exit(code=1)
    settings = get_settings()
    with Neo4jClient(settings.neo4j) as client:
        _require_neo4j(client)
        Migrator().reset(client)
    typer.echo("cleared")


@app.command()
def query(
    name: str = typer.Argument(
        ...,
        help="One of: covers, covered-by, endpoints, calling, dependents",
    ),
    target: str = typer.Argument(..., help="Node id (Service / CodeArtifact / TestCase)."),
    depth: int = typer.Option(5, help="Max depth for 'dependents'."),
) -> None:
    """Run a named read query and print the result as JSON."""
    settings = get_settings()
    with Neo4jClient(settings.neo4j) as client:
        _require_neo4j(client)
        q = GraphQueries(client)
        result: Any
        if name == "covers":
            result = [t.__dict__ for t in q.tests_covering(target)]
        elif name == "covered-by":
            result = [a.__dict__ for a in q.artifacts_covered_by(target)]
        elif name == "endpoints":
            result = [a.__dict__ for a in q.service_endpoints(target)]
        elif name == "calling":
            result = q.services_calling(target)
        elif name == "dependents":
            result = [d.__dict__ for d in q.transitive_dependents(target, max_depth=depth)]
        else:
            typer.echo(f"unknown query: {name}", err=True)
            raise typer.Exit(code=2)
    typer.echo(json.dumps(result, indent=2, default=str))


def _require_neo4j(client: Neo4jClient) -> None:
    if not client.healthcheck():
        typer.secho(
            f"cannot reach Neo4j at {client.uri}. Is it running? Try `make neo4j-up`.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)


def _read_merged(directory: Path) -> MergedResult:
    merged = MergedResult()
    _load_into(merged.services, directory / "services.json", Service)
    _load_into(merged.connections, directory / "connections.json", ExternalConnection)
    _load_into(merged.artifacts, directory / "artifacts.json", CodeArtifact)
    _load_into(merged.tests, directory / "tests.json", TestCase)
    _load_into(merged.endpoints, directory / "endpoints.json", Endpoint)
    _load_into(merged.data_models, directory / "data_models.json", DataModel)
    _load_into(merged.queries, directory / "queries.json", Query)
    _load_into(merged.kafka_topics, directory / "kafka_topics.json", KafkaTopic)
    _load_into(merged.kafka_producers, directory / "kafka_producers.json", KafkaProducer)
    _load_into(merged.kafka_consumers, directory / "kafka_consumers.json", KafkaConsumer)
    _load_into(merged.mocks, directory / "mocks.json", Mock)
    _load_into(merged.suggestions, directory / "suggestions.json", Suggestion)
    return merged


def _load_into(bucket: dict, path: Path, model) -> None:
    if not path.exists():
        return
    for row in json.loads(path.read_text()):
        item = model.model_validate(row)
        bucket[item.id] = item


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
