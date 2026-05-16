# system-graph

Multi-repo impact analysis system: a Neo4j-backed dependency graph for smart polyrepo test selection.

When a developer pushes to one service, `system-graph` answers: *which other services and tests are actually affected?* It pulls real production traces from Datadog, parses source code and test files from GitHub, stores the resulting dependency graph in Neo4j, and selects the minimal set of tests to run in CI.

See `docs/` for the full design (`QUICK_REFERENCE.md` is the best place to start, then `complete_system_design.md`).

## Status

Phase 1 — **data ingestion** — is in progress:

- `core/types/` — Pydantic v2 domain model (`Service`, `ExternalConnection`, `CodeArtifact`, `TestCase`, `Change`, `ImpactAnalysisResult`).
- `core/adapters/` — Adapter framework (base class, registry, merger, mapper, validator, confidence scorer).
- `core/config/` — Env-driven settings.
- `ingestion/adapters/datadog/` — Extracts inter-service calls from Datadog APM spans.
- `ingestion/adapters/github/` — Pulls repo metadata and source files from GitHub.
- `ingestion/adapters/testparser/` — Walks local repo checkouts, classifies tests UNIT/COMPONENT/INTEGRATION.

Later phases (Neo4j loader, impact rules, test selector, API, webhook, dashboard) are not implemented yet.

## Quick start

```bash
make dev              # create .venv and install runtime + dev deps
cp .env.example .env  # then fill in DD_API_KEY, GITHUB_TOKEN, etc.
make neo4j-up         # boot Neo4j locally on bolt://localhost:7687
make test             # run pytest
```

Tests that hit external services (Datadog, GitHub, Neo4j) are marked `@pytest.mark.integration` and are skipped unless credentials are set.

## Layout

```
core/
  types/          # Pydantic domain model
  adapters/       # IngestionAdapter ABC, registry, merger, mapper, validator, confidence
  config/         # pydantic-settings
ingestion/
  adapters/
    datadog/      # APM trace ingestion
    github/       # repo + source ingestion
    testparser/   # AST-based test classification
  parsers/        # language parsers (python today; go/java/ts later)
docs/             # design specs
```
