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

## How to run

Two CLIs ship in the venv after `make dev`: `sg-ingest` (read sources → JSON
in `./out/`) and `sg-graph` (manage Neo4j: init schema, load JSON, query).
Both use settings from `.env`. Replace the `< >` placeholders below.

### 1. Ingest one or more repos

`testparser` treats every subdirectory of `TESTPARSER_ROOT` as a separate
repo. Point it at a *parent* dir, not a single repo.

```bash
# Template
TESTPARSER_ROOT=<parent-dir-of-repos> \
  .venv/bin/sg-ingest --out ./out \
                      --skip datadog --skip github   # omit to enable them

# Example: the demo fixture (one repo: sample-payment-service)
TESTPARSER_ROOT=./data .venv/bin/sg-ingest --out ./out --skip datadog --skip github
```

To ingest a single repo that doesn't live inside a parent dir, symlink it
under a wrapper:

```bash
mkdir -p /tmp/sg-in && ln -sfn <absolute-repo-path> /tmp/sg-in/<repo-id>
TESTPARSER_ROOT=/tmp/sg-in .venv/bin/sg-ingest --out ./out --skip datadog --skip github
```

Output lands in `./out/{services,artifacts,endpoints,data_models,queries,
kafka_topics,kafka_producers,kafka_consumers,mocks,tests,connections,
suggestions,relationships}.json`.

### 2. Load into Neo4j

```bash
make neo4j-up                                     # boot Neo4j if not already
.venv/bin/sg-graph init                           # apply pending migrations
.venv/bin/sg-graph load --from ./out              # MERGE everything (idempotent)
.venv/bin/sg-graph status                         # node + edge counts
```

To wipe and reload from scratch:

```bash
.venv/bin/sg-graph clear --yes && .venv/bin/sg-graph load --from ./out
```

### 3. Query the graph

Built-in named queries via the CLI:

```bash
# Template
.venv/bin/sg-graph query <name> <node-id> [--depth N]

# Names: covers | covered-by | endpoints | calling | dependents
.venv/bin/sg-graph query covers     <test-id>
.venv/bin/sg-graph query covered-by <code-artifact-id>
.venv/bin/sg-graph query endpoints  <service-id>
.venv/bin/sg-graph query dependents <code-artifact-id> --depth 5
```

Or open the Neo4j browser at <http://localhost:7474> and run Cypher
directly:

```cypher
// Impact: what tests rerun if I change <function-name>?
MATCH (target:CodeArtifact {name:'<function-name>'})
MATCH (t:TestCase)-[:COVERS]->(:CodeArtifact)-[:CALLS*0..]->(target)
RETURN DISTINCT t.name, t.file

// All endpoints in a service
MATCH (s:Service {id:'<service-id>'})-[:CONTAINS]->(e:Endpoint)
      -[:HANDLED_BY]->(h:CodeArtifact)
RETURN e.method, e.path, h.name, h.file
```

### 4. Self-ingest (the system analyzing itself)

```bash
mkdir -p /tmp/sg-self && ln -sfn "$(pwd)" /tmp/sg-self/system-graph
TESTPARSER_ROOT=/tmp/sg-self .venv/bin/sg-ingest --out ./out --skip datadog --skip github
.venv/bin/sg-graph clear --yes && .venv/bin/sg-graph load --from ./out
```

### 5. Optional: LLM enhance pass

The enhance pass proposes candidate edges for ambiguities the deterministic
resolvers couldn't bind (e.g. unresolved cross-file calls). It runs
automatically through the `LLMEnhancer` Python API; the CLI hookup is
pending. Without `ANTHROPIC_API_KEY` it short-circuits to `NullClient` and
produces zero suggestions (clean no-op).

```bash
export ANTHROPIC_API_KEY=<your-key>
# Then call core.llm.LLMEnhancer programmatically; see core/llm/enhance.py
```

LLM-suggested edges are tagged `source="llm"` so Cypher queries can filter:

```cypher
// Just the deterministic edges
MATCH (a)-[r {source:'resolver'}]->(b) RETURN a, r, b

// Just the AI guesses with their confidence
MATCH (a)-[r {source:'llm'}]->(b)
RETURN a.id, type(r), b.id, r.confidence, r.reason ORDER BY r.confidence DESC
```

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
