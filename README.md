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

### With `make` (Linux / macOS)

```bash
make dev              # create .venv and install runtime + dev deps
cp .env.example .env  # then fill in DD_API_KEY, GITHUB_TOKEN, etc.
make neo4j-up         # boot Neo4j locally on bolt://localhost:7687
make test             # run pytest
```

### Without `make` (Linux / macOS shells)

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pip install -e .
cp .env.example .env                              # fill in keys
docker compose up -d neo4j                        # or: podman compose up -d neo4j
.venv/bin/pytest                                  # run tests
```

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\pip.exe install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\pip.exe install -e .
Copy-Item .env.example .env                       # then edit and fill in keys
docker compose up -d neo4j
.venv\Scripts\pytest.exe
```

### Windows (cmd.exe)

```cmd
py -3 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\pip install -e .
copy .env.example .env
docker compose up -d neo4j
.venv\Scripts\pytest
```

> **Path conventions below**: examples use `.venv/bin/<tool>` (Linux/macOS). On Windows substitute `.venv\Scripts\<tool>.exe`, or activate the venv first (`.venv\Scripts\Activate.ps1` in PowerShell) and drop the prefix entirely.

Tests that hit external services (Datadog, GitHub, Neo4j) are marked `@pytest.mark.integration` and are skipped unless credentials are set.

## How to run

Two CLIs ship in the venv: `sg-ingest` (read sources → JSON in `./out/`)
and `sg-graph` (manage Neo4j: init schema, load JSON, query). Both use
settings from `.env`. Replace the `< >` placeholders below.

Windows users: substitute every `.venv/bin/<tool>` with
`.venv\Scripts\<tool>.exe`, or activate the venv once
(`.venv\Scripts\Activate.ps1` in PowerShell) and drop the prefix.

### 1. Ingest one or more repos

`testparser` treats every subdirectory of `TESTPARSER_ROOT` as a separate
repo. Point it at a *parent* dir, not a single repo.

**Linux / macOS:**
```bash
TESTPARSER_ROOT=<parent-dir-of-repos> \
  .venv/bin/sg-ingest --out ./out \
                      --skip datadog --skip github   # omit to enable them

# Example: the demo fixture (one repo: sample-payment-service)
TESTPARSER_ROOT=./data .venv/bin/sg-ingest --out ./out --skip datadog --skip github
```

**Windows PowerShell:**
```powershell
$env:TESTPARSER_ROOT = "<parent-dir-of-repos>"
.venv\Scripts\sg-ingest.exe --out .\out --skip datadog --skip github
```

**Windows cmd.exe:**
```cmd
set TESTPARSER_ROOT=<parent-dir-of-repos>
.venv\Scripts\sg-ingest --out .\out --skip datadog --skip github
```

To ingest a single repo that doesn't live inside a parent dir, symlink
(Linux/macOS) or create a junction (Windows) under a wrapper:

```bash
# Linux/macOS
mkdir -p /tmp/sg-in && ln -sfn <absolute-repo-path> /tmp/sg-in/<repo-id>
TESTPARSER_ROOT=/tmp/sg-in .venv/bin/sg-ingest --out ./out --skip datadog --skip github
```

```powershell
# Windows PowerShell (junctions don't need admin)
New-Item -ItemType Directory -Force $env:TEMP\sg-in
New-Item -ItemType Junction -Path "$env:TEMP\sg-in\<repo-id>" -Target "<absolute-repo-path>"
$env:TESTPARSER_ROOT = "$env:TEMP\sg-in"
.venv\Scripts\sg-ingest.exe --out .\out --skip datadog --skip github
```

Output lands in `./out/{services,artifacts,endpoints,data_models,queries,
kafka_topics,kafka_producers,kafka_consumers,mocks,tests,connections,
suggestions,relationships}.json`.

### 2. Datadog ingestion (optional)

The Datadog adapter uses a SQLite staging store (`./out/datadog.db`) so you
can fetch once and replay parsing without re-burning API quota. Three
subcommands:

```bash
# Pull spans + service catalog into the staging store.
# By default: both APIs. Use --no-spans or --no-catalog to scope down.
.venv/bin/sg-ingest datadog-fetch --lookback-hours 24

# Parse staged spans → services.json + connections.json (no network).
.venv/bin/sg-ingest datadog-parse --out ./out

# Quick sanity check: pull a small window, print services + top
# connections + protocol histogram. Writes nothing.
.venv/bin/sg-ingest datadog-preview --lookback-hours 1
```

`.env` knobs:

```ini
DD_API_KEY=...
DD_APP_KEY=...
DD_SITE=datadoghq.com               # or datadoghq.eu, ddog-gov.com, ...
DD_ENV=prod                         # filter spans to this env tag (empty = all)
DD_TRACE_LOOKBACK_HOURS=720         # default lookback when fetching spans
DD_SPANS_TTL_SECONDS=300            # spans cache freshness (5 min default)
DD_CATALOG_TTL_SECONDS=3600         # service catalog freshness (1 h default)
DD_STORE_PATH=./out/datadog.db      # SQLite staging path
```

### 3. Load into Neo4j

```bash
# Linux/macOS — make available
make neo4j-up

# Or directly (works everywhere docker/podman is installed)
docker compose up -d neo4j

.venv/bin/sg-graph init                           # apply pending migrations
.venv/bin/sg-graph load --from ./out              # MERGE everything (idempotent)
.venv/bin/sg-graph status                         # node + edge counts
```

To wipe and reload from scratch:

```bash
.venv/bin/sg-graph clear --yes && .venv/bin/sg-graph load --from ./out
```

### 4. Query the graph

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
directly. See the next section for ready-to-paste queries.

### 5. Self-ingest (the system analyzing itself)

```bash
# Linux/macOS
mkdir -p /tmp/sg-self && ln -sfn "$(pwd)" /tmp/sg-self/system-graph
TESTPARSER_ROOT=/tmp/sg-self .venv/bin/sg-ingest --out ./out --skip datadog --skip github
.venv/bin/sg-graph clear --yes && .venv/bin/sg-graph load --from ./out
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force $env:TEMP\sg-self
New-Item -ItemType Junction -Path "$env:TEMP\sg-self\system-graph" -Target "$pwd"
$env:TESTPARSER_ROOT = "$env:TEMP\sg-self"
.venv\Scripts\sg-ingest.exe --out .\out --skip datadog --skip github
.venv\Scripts\sg-graph.exe clear --yes
.venv\Scripts\sg-graph.exe load --from .\out
```

## Basic queries

Paste into the Neo4j browser at <http://localhost:7474>. Replace
`<placeholder>` values with real IDs from `sg-graph status` or earlier
query output.

### Impact: what to retest

```cypher
// Tests that exercise <function-name> directly or through any call chain.
MATCH (target:CodeArtifact {name:'<function-name>'})
MATCH (t:TestCase)-[:COVERS]->(:CodeArtifact)-[:CALLS*0..]->(target)
RETURN DISTINCT t.name, t.file
```

```cypher
// Every endpoint reachable from a changed function (the prod-facing
// blast radius), capped at 6 hops to keep results readable.
MATCH (changed:CodeArtifact {name:'<function-name>'})
MATCH (e:Endpoint)-[:HANDLED_BY]->(h:CodeArtifact)-[:CALLS*0..6]->(changed)
RETURN DISTINCT e.method, e.path, e.framework
```

### N-hop subgraph from a single node

```cypher
// Requires APOC (already enabled in docker-compose).
MATCH (start {id:'<node-id>'})
CALL apoc.path.subgraphAll(start, {
  maxLevel: 5,
  labelFilter: '-Service'   // exclude Service umbrella nodes for clarity
}) YIELD nodes, relationships
RETURN nodes, relationships
```

```cypher
// Pure-Cypher alternative (no APOC), outbound only. `*1..5` must be a
// literal — change the number, not a parameter.
MATCH p = (start {id:'<node-id>'})-[*1..5]->(n)
RETURN p
```

### Endpoints in a service

```cypher
MATCH (s:Service {id:'<service-id>'})-[:CONTAINS]->(e:Endpoint)
      -[:HANDLED_BY]->(h:CodeArtifact)
RETURN e.method, e.path, h.name, h.file
ORDER BY e.path
```

### Traced traffic → code (the Datadog correlation)

```cypher
// Which staged Datadog connection actually hits which code endpoint?
MATCH (c:ExternalConnection)-[:TARGETS_ENDPOINT]->(e:Endpoint)
RETURN c.source_service_id  AS source,
       e.method + ' ' + e.path AS endpoint,
       c.frequency             AS calls_per_min,
       c.criticality
ORDER BY c.frequency DESC
LIMIT 25
```

```cypher
// External connections without an Endpoint match — gaps in static
// analysis or in the trace data. Worth investigating.
MATCH (c:ExternalConnection)
WHERE c.target_endpoint_id = '' AND c.target_service_id <> ''
RETURN c.source_service_id, c.target_service_id, c.endpoint, c.frequency
ORDER BY c.frequency DESC
```

### Service Catalog views (phase 3)

```cypher
// Services owned by a team, ranked by criticality tier.
MATCH (s:Service)
WHERE s.team = '<team>'
RETURN s.name, s.tier, s.owner, s.repo_url
ORDER BY s.tier
```

```cypher
// Tier-1 services with no traced traffic yet — known to the catalog
// but cold in this lookback window.
MATCH (s:Service {tier:'tier-1', is_active:false})
RETURN s.name, s.team, s.owner
```

```cypher
// Cross-team dependencies (caller and callee owned by different teams).
MATCH (src:Service)-[:INITIATES]->(c:ExternalConnection)-[:TARGETS]->(dst:Service)
WHERE src.team <> '' AND dst.team <> '' AND src.team <> dst.team
RETURN src.team AS calls_from, dst.team AS calls_into,
       count(*) AS edges
ORDER BY edges DESC
```

### Counts (sanity check after a load)

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY n DESC;
```

```cypher
MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS n ORDER BY n DESC;
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
