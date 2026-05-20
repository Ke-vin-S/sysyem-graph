"""Typer CLI for running Phase 1 ingestion.

Phase 1 stops at producing in-memory + JSON-on-disk records; the Neo4j
loader is Phase 2 work. For now `sg-ingest run --out ./out` dumps merged
JSON files that the loader will later consume.
"""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import typer

from core.adapters import AdapterRegistry, IngestionContext
from core.config import get_settings
from core.types.errors import ConfigurationError
from ingestion.adapters.datadog import (
    DatadogAdapter,
    DatadogAdapterConfig,
    DatadogClient,
    DatadogStore,
)
from ingestion.adapters.datadog.fetcher import DatadogFetcher
from ingestion.adapters.datadog.parser import DatadogParser
from ingestion.adapters.datadog.trace_parser import TraceParser
from ingestion.adapters.github import (
    AuthError,
    AuthVerifier,
    GitHubAdapter,
    GitHubAdapterConfig,
    GitHubService,
    GitHubStore,
    RepoCloner,
    TokenResolver,
    host_of,
)
from ingestion.adapters.testparser import TestParserAdapter, TestParserAdapterConfig

# `invoke_without_command=True` lets `sg-ingest --out ./out --skip datadog`
# keep working after we added the `datadog-preview` sibling command — the
# callback accepts the same options as `run` and falls through to it when
# no explicit subcommand is given.
app = typer.Typer(
    help="system-graph ingestion CLI",
    invoke_without_command=True,
    no_args_is_help=False,
)
logger = logging.getLogger(__name__)


@app.callback()
def _root(
    ctx: typer.Context,
    out: Path = typer.Option(Path("./out"), help="Directory to write merged records as JSON."),
    skip: list[str] = typer.Option(  # noqa: B008
        [],
        "--skip",
        help="Adapter identifier(s) to disable for this run (e.g. --skip datadog).",
    ),
) -> None:
    """If no subcommand is given, run the full ingestion pipeline (legacy default)."""
    if ctx.invoked_subcommand is None:
        _do_run(out=out, skip=skip)


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
    _do_run(out=out, skip=skip)


def _do_run(*, out: Path, skip: list[str]) -> None:
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


@app.command("datadog-preview")
def datadog_preview(
    lookback_hours: int = typer.Option(
        1, "--lookback-hours", help="How far back to query Datadog (default 1h)."
    ),
    env: str = typer.Option(
        "",
        "--env",
        help="Override DD_ENV for this preview run. Empty = use the env var / no filter.",
    ),
    top: int = typer.Option(10, "--top", help="Number of busiest connections to print."),
    services_only: bool = typer.Option(
        False, "--services-only", help="Skip the connection-level dump; print services only."
    ),
) -> None:
    """Pull a small Datadog window, print a summary, write nothing to disk.

    Use this before a real `run` to sanity-check that your DD_API_KEY,
    DD_APP_KEY, DD_SITE, and DD_ENV are pulling the data you expect.
    """
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    if not settings.datadog.enabled:
        typer.secho(
            "datadog disabled: set DD_API_KEY and DD_APP_KEY in your environment.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    cfg = DatadogAdapterConfig.from_settings(settings.datadog)
    cfg = DatadogAdapterConfig(
        api_key=cfg.api_key,
        app_key=cfg.app_key,
        site=cfg.site,
        lookback_hours=lookback_hours,
        min_span_count=1,
        services_allowlist=cfg.services_allowlist,
        env=env or cfg.env,
    )

    adapter = DatadogAdapter(cfg)
    typer.echo(f"querying datadog: lookback={lookback_hours}h env={cfg.env or '<all>'}")
    result = adapter.extract(IngestionContext())

    typer.echo("")
    typer.echo(f"services observed: {len(result.services)}")
    for svc in result.services:
        typer.echo(f"  - {svc.id}")

    if services_only:
        return

    connections = sorted(
        result.connections,
        key=lambda c: float(c.data_flow.get("spans_observed", "0") or 0),
        reverse=True,
    )
    typer.echo("")
    typer.echo(f"connections observed: {len(connections)}")

    proto_hist: Counter[str] = Counter(c.protocol for c in connections)
    typer.echo("by protocol: " + ", ".join(f"{p}={n}" for p, n in proto_hist.most_common()))

    typer.echo("")
    typer.echo(f"top {min(top, len(connections))} connections by span count:")
    for c in connections[:top]:
        target = c.target_service_id or f"({c.target_name})"
        typer.echo(
            f"  {c.source_service_id:20s} -> {target:25s} {c.endpoint:30s} "
            f"spans={c.data_flow.get('spans_observed', '?')} "
            f"err={c.data_flow.get('error_rate', '?')}"
        )

    if result.warnings:
        typer.echo("")
        for w in result.warnings:
            typer.secho(f"warning: {w}", fg=typer.colors.YELLOW)


@app.command("datadog-fetch")
def datadog_fetch(
    lookback_hours: int = typer.Option(
        0,
        "--lookback-hours",
        help="Override the configured lookback. 0 = use DD_TRACE_LOOKBACK_HOURS.",
    ),
    env: str = typer.Option("", "--env", help="Override DD_ENV for this fetch."),
    force: bool = typer.Option(
        False, "--force", help="Fetch even if the staged data is still within its TTL."
    ),
    spans: bool = typer.Option(True, "--spans/--no-spans", help="Fetch APM spans."),
    catalog: bool = typer.Option(
        True, "--catalog/--no-catalog", help="Fetch the Service Catalog."
    ),
) -> None:
    """Pull Datadog data into the staging store. No parsing, no JSON.

    By default fetches both spans and the Service Catalog. Use
    `--no-spans` / `--no-catalog` to skip individual APIs, or `--force`
    to bypass TTL freshness checks.
    """
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    if not settings.datadog.enabled:
        typer.secho("datadog disabled: set DD_API_KEY and DD_APP_KEY.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    cfg = DatadogAdapterConfig.from_settings(settings.datadog)
    if lookback_hours > 0:
        cfg = _replace_cfg(cfg, lookback_hours=lookback_hours)
    if env:
        cfg = _replace_cfg(cfg, env=env)

    store = DatadogStore(cfg.store_path)
    client = DatadogClient(api_key=cfg.api_key, app_key=cfg.app_key, site=cfg.site)
    fetcher = DatadogFetcher(store, client)

    if spans:
        if not force and not store.is_stale("spans", ttl_seconds=cfg.spans_ttl_seconds):
            last = store.last_fetched_at("spans")
            typer.echo(
                f"spans cache fresh (last {last.isoformat() if last else 'never'}); "
                "skipping. Use --force to refetch."
            )
        else:
            adapter = DatadogAdapter(cfg, client=client, store=store, fetcher=fetcher)
            query = adapter._build_query(IngestionContext())  # noqa: SLF001
            typer.echo(
                f"fetching spans: lookback={cfg.lookback_hours}h env={cfg.env or '<all>'} "
                f"query={query!r}"
            )
            count = fetcher.fetch_spans(
                lookback_hours=cfg.lookback_hours,
                query=query,
                env=cfg.env,
            )
            typer.echo(f"  wrote {count} spans")

    if catalog:
        if not force and not store.is_stale("catalog", ttl_seconds=cfg.catalog_ttl_seconds):
            last = store.last_fetched_at("catalog")
            typer.echo(
                f"catalog cache fresh (last {last.isoformat() if last else 'never'}); "
                "skipping. Use --force to refetch."
            )
        else:
            typer.echo("fetching service catalog")
            count = fetcher.fetch_catalog()
            typer.echo(f"  wrote {count} catalog entries")

    typer.echo(f"store: {cfg.store_path}")


@app.command("datadog-parse")
def datadog_parse(
    out: Path = typer.Option(
        Path("./out"), help="Where to write services.json + connections.json."
    ),
    lookback_hours: int = typer.Option(
        0,
        "--lookback-hours",
        help="Window to parse over (default: DD_TRACE_LOOKBACK_HOURS).",
    ),
    env: str = typer.Option("", "--env", help="Filter parsed spans to this env tag."),
) -> None:
    """Parse staged spans into Service/ExternalConnection JSON. No network.

    Reads from the same SQLite store `datadog-fetch` populated, so you
    can iterate on parser logic without re-pulling spans.
    """
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    if not settings.datadog.enabled:
        # Keys aren't strictly required for parsing — we never call the API —
        # but config plumbing assumes them. Allow parse-only when store exists.
        cfg = DatadogAdapterConfig(
            api_key="",
            app_key="",
            site=settings.datadog.site,
            lookback_hours=lookback_hours or settings.datadog.trace_lookback_hours,
            env=env or settings.datadog.env,
            store_path=settings.datadog.store_path,
        )
    else:
        cfg = DatadogAdapterConfig.from_settings(settings.datadog)
        if lookback_hours > 0:
            cfg = _replace_cfg(cfg, lookback_hours=lookback_hours)
        if env:
            cfg = _replace_cfg(cfg, env=env)

    store = DatadogStore(cfg.store_path)
    if store.span_count() == 0:
        typer.secho(
            "no spans in store — run `sg-ingest datadog-fetch` first.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=2)

    trace_parser = TraceParser(
        lookback_hours=cfg.lookback_hours, min_span_count=cfg.min_span_count
    )
    parser = DatadogParser(store, trace_parser)
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=cfg.lookback_hours)
    result = parser.parse(since=since, env=cfg.env or None, now=now)

    out.mkdir(parents=True, exist_ok=True)
    _dump(out / "services.json", [s.model_dump(mode="json") for s in result.services])
    _dump(out / "connections.json", [c.model_dump(mode="json") for c in result.connections])
    typer.echo(
        f"parsed: {result.spans_seen} spans -> "
        f"{len(result.services)} services, {len(result.connections)} connections "
        f"(skipped={result.spans_skipped})"
    )


def _replace_cfg(cfg: DatadogAdapterConfig, **kwargs: Any) -> DatadogAdapterConfig:
    """Tiny helper so the CLI subcommands can layer overrides onto the
    settings-derived config without re-writing field-by-field."""
    return DatadogAdapterConfig(
        api_key=kwargs.get("api_key", cfg.api_key),
        app_key=kwargs.get("app_key", cfg.app_key),
        site=kwargs.get("site", cfg.site),
        lookback_hours=kwargs.get("lookback_hours", cfg.lookback_hours),
        min_span_count=kwargs.get("min_span_count", cfg.min_span_count),
        services_allowlist=kwargs.get("services_allowlist", cfg.services_allowlist),
        env=kwargs.get("env", cfg.env),
        spans_ttl_seconds=kwargs.get("spans_ttl_seconds", cfg.spans_ttl_seconds),
        catalog_ttl_seconds=kwargs.get("catalog_ttl_seconds", cfg.catalog_ttl_seconds),
        store_path=kwargs.get("store_path", cfg.store_path),
    )


# ---- github sub-app --------------------------------------------------------

github_app = typer.Typer(
    help="Manage and ingest GitHub repositories (clone-based, incremental).",
    no_args_is_help=True,
)
app.add_typer(github_app, name="github")


def _build_github_service() -> tuple[GitHubService, GitHubAdapterConfig]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    cfg = GitHubAdapterConfig.from_settings(settings.github)
    store = GitHubStore(cfg.store_path)
    cloner = RepoCloner(clones_dir=cfg.clones_dir)
    return GitHubService(store=store, cloner=cloner), cfg


@github_app.command("add")
def github_add(
    url: str = typer.Argument(..., help="Repo URL or owner/name."),
    branch: str = typer.Option(
        "", "--branch", help="Track this branch instead of remote HEAD."
    ),
) -> None:
    """Register a repository for future ingestion. No clone happens yet."""
    service, _ = _build_github_service()
    record = service.add_repo(url, branch=branch)
    typer.echo(f"added {record.url}")
    typer.echo(f"  owner/name: {record.owner}/{record.name}")
    typer.echo(f"  clone_path: {record.clone_path}")


@github_app.command("list")
def github_list() -> None:
    """Show every registered repository."""
    service, _ = _build_github_service()
    records = service.list_repos()
    if not records:
        typer.echo("no repos registered.")
        return
    typer.echo(f"{'URL':60s} {'SHA':10s} {'STATUS':12s} INGESTED")
    for r in records:
        sha = (r.last_commit_sha or "—")[:8]
        ingested = r.last_ingested_at or "—"
        typer.echo(f"{r.url:60s} {sha:10s} {r.status:12s} {ingested}")


@github_app.command("remove")
def github_remove(
    url: str = typer.Argument(..., help="Repo URL or owner/name to remove."),
    keep_clone: bool = typer.Option(
        False, "--keep-clone", help="Leave the on-disk clone in place."
    ),
) -> None:
    """Unregister a repo and (by default) delete its clone."""
    service, _ = _build_github_service()
    if service.remove_repo(url, delete_clone=not keep_clone):
        typer.echo(f"removed {url}")
    else:
        typer.secho(f"not found: {url}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)


@github_app.command("clean")
def github_clean(
    url: str = typer.Argument("", help="Specific repo to clean. Omit with --all."),
    all_repos: bool = typer.Option(False, "--all", help="Wipe every clone."),
) -> None:
    """Wipe on-disk clone(s) but keep DB rows. Next ingest re-clones."""
    if not url and not all_repos:
        typer.secho("specify a URL or pass --all", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    service, _ = _build_github_service()
    removed = service.clean_clones(url=None if all_repos else url)
    typer.echo(f"cleaned {removed} clone(s)")


@github_app.command("status")
def github_status() -> None:
    """Disk + ingest state for every registered repo."""
    service, cfg = _build_github_service()
    statuses = service.get_status()
    if not statuses:
        typer.echo("no repos registered.")
        return
    typer.echo(f"clones_dir: {cfg.clones_dir}")
    typer.echo("")
    typer.echo(f"{'URL':50s} {'STATUS':10s} {'CLONE':6s} {'SIZE':>10s}  NEEDS_INGEST")
    for s in statuses:
        size = _human_size(s.clone_size_bytes)
        clone = "yes" if s.clone_exists else "no"
        flag = "yes" if s.needs_ingest else "no"
        typer.echo(f"{s.record.url:50s} {s.record.status:10s} {clone:6s} {size:>10s}  {flag}")


# ---- `sg-ingest github auth …` ---------------------------------------------

auth_app = typer.Typer(
    help="Manage and validate GitHub PATs (per host).",
    no_args_is_help=True,
)
github_app.add_typer(auth_app, name="auth")


@auth_app.command("check")
def github_auth_check(
    url: str = typer.Argument(
        "",
        help="Clone URL to derive the host from. Mutually exclusive with --host.",
    ),
    host: str = typer.Option(
        "",
        "--host",
        help="GitHub host to validate against (e.g. github.com, ghe.acme.com).",
    ),
) -> None:
    """Hit `<host>/user` to confirm the resolved PAT works.

    Prints the authenticated login and the token's OAuth scopes. Use this
    BEFORE registering private repos to catch token issues early."""
    if url and host:
        typer.secho("pass either URL or --host, not both", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    target_host = host or (host_of(url) if url else "github.com")
    if not target_host:
        typer.secho(f"could not derive host from {url!r}", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    resolver = TokenResolver()
    token = resolver.resolve(f"https://{target_host}/_/_")
    verifier = AuthVerifier()
    try:
        outcome = verifier.check(target_host, token)
    except AuthError as exc:
        typer.secho(f"error: {exc.hint}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from None

    scopes = ", ".join(outcome.scopes) if outcome.scopes else "(no scopes header)"
    typer.echo(f"logged in as {outcome.login} on {outcome.host}")
    typer.echo(f"  api:    {outcome.api_url}")
    typer.echo(f"  scopes: {scopes}")


@auth_app.command("show")
def github_auth_show() -> None:
    """List token-configuration status for every known host.

    Known hosts = github.com plus any host appearing in registered repos."""
    service, _ = _build_github_service()
    hosts: set[str] = {"github.com"}
    for record in service.list_repos():
        h = host_of(record.url)
        if h:
            hosts.add(h)

    resolver = TokenResolver()
    typer.echo(f"{'HOST':30s} {'CONFIGURED':12s} ENV VAR")
    for h in sorted(hosts):
        configured = resolver.is_configured(h)
        env_var = resolver.env_var_for(h)
        flag = "yes" if configured else "no"
        suffix = "" if configured else f"  (set {env_var})"
        typer.echo(f"{h:30s} {flag:12s} {env_var}{suffix}")


@github_app.command("ingest")
def github_ingest(
    url: str = typer.Argument("", help="Specific repo to ingest. Omit with --all."),
    all_repos: bool = typer.Option(False, "--all", help="Ingest every registered repo."),
    out: Path = typer.Option(
        Path("./out"), help="Directory to write the merged records as JSON."
    ),
) -> None:
    """Clone-if-needed and ingest into the merged records.

    Runs ONLY the GitHub adapter (other adapters are skipped). Use plain
    `sg-ingest` or `sg-ingest run` for the full pipeline."""
    if not url and not all_repos:
        typer.secho("specify a URL or pass --all", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    cfg = GitHubAdapterConfig.from_settings(settings.github)

    registry = AdapterRegistry()
    adapter = GitHubAdapter(cfg)
    registry.register(adapter)

    repos = None if all_repos else (url,)
    report = registry.run_all(IngestionContext(repos=repos))
    typer.echo(f"counts: {report.merged.counts()}")
    if report.failures:
        typer.secho(f"failures: {report.failures}", fg=typer.colors.RED)

    out.mkdir(parents=True, exist_ok=True)
    merged = report.merged
    services = list(merged.services.values())
    artifacts = list(merged.artifacts.values())
    _dump(out / "services.json", [s.model_dump(mode="json") for s in services])
    _dump(out / "artifacts.json", [a.model_dump(mode="json") for a in artifacts])
    typer.echo(f"wrote {len(services)} services and {len(artifacts)} artifacts to {out}")

    if report.failures:
        raise typer.Exit(code=1)


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} TiB"


def _register_adapters(
    registry: AdapterRegistry, settings: Any, skipped: set[str]
) -> None:
    if "datadog" not in skipped and settings.datadog.enabled:
        cfg = DatadogAdapterConfig.from_settings(settings.datadog)
        store = DatadogStore(cfg.store_path)
        registry.register(DatadogAdapter(cfg, store=store))
    if "github" not in skipped:
        cfg = GitHubAdapterConfig.from_settings(settings.github)
        # Register the adapter when EITHER there are seed repos (legacy
        # env-driven flow) OR the store already knows about some repos
        # (added via `sg-ingest github add`).
        store = GitHubStore(cfg.store_path)
        try:
            has_repos = bool(cfg.repos) or bool(store.list_repos())
        finally:
            store.close()
        if has_repos:
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
        for tbl_id in artifact.reads:
            edges.append({"src": artifact.id, "rel": "READS", "dst": tbl_id})
        for tbl_id in artifact.writes:
            edges.append({"src": artifact.id, "rel": "WRITES", "dst": tbl_id})

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
        if conn.target_endpoint_id:
            edges.append(
                {"src": conn.id, "rel": "TARGETS_ENDPOINT", "dst": conn.target_endpoint_id}
            )

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
