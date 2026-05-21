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
- `ingestion/adapters/github/` — Shallow-clones GitHub repos into a local cache (`./out/github_repos/`), tracks last-ingested SHA in `./out/github.db`, skips re-ingest when the remote HEAD hasn't moved.
- `ingestion/adapters/testparser/` — Walks local repo checkouts, classifies tests UNIT/COMPONENT/INTEGRATION.

Later phases (Neo4j loader, impact rules, test selector, API, webhook, dashboard) are not implemented yet.

## Quick start

The recommended path is the **containerized dev environment** — same Linux
toolchain everywhere, so resolvers that depend on path / filesystem
semantics produce identical output on Linux, macOS, and Windows hosts.

### Containerized (recommended — Linux / macOS / Windows)

Prerequisites: Docker Desktop or Podman 4.4+ (with `podman compose`).

```bash
# One-time: build the dev image (~2 min, cached after that).
docker compose --profile dev build dev

# Run the tests.
docker compose --profile dev run --rm dev pytest

# Drop into a shell — sg-ingest / sg-graph are on the PATH inside.
docker compose --profile dev run --rm dev bash
```

On Windows / Podman, swap `docker compose` for `podman compose`:

```powershell
podman compose --profile dev build dev
podman compose --profile dev run --rm dev pytest
```

Neo4j is in the same compose file; the dev container talks to it on
`bolt://neo4j:7687` (service name, not localhost). It's brought up
automatically as a dependency.

`make` shortcuts (work with both runtimes — override `COMPOSE` for Podman):

```bash
make docker-build                          # docker compose path
make COMPOSE="podman compose" docker-test  # podman path
make docker-shell                          # interactive shell
make docker-ingest                         # ingest ./data into Neo4j
```

### Host venv (Linux / macOS, with `make`)

If you'd rather not use containers and have `make` + Python 3.10+:

```bash
make dev              # create .venv and install runtime + dev deps
cp .env.example .env  # then fill in DD_API_KEY, GITHUB_TOKEN, etc.
make neo4j-up         # boot Neo4j locally on bolt://localhost:7687
make test             # run pytest
```

### Host venv (Linux / macOS, no `make`)

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pip install -e .
cp .env.example .env                              # fill in keys
docker compose up -d neo4j                        # or: podman compose up -d neo4j
.venv/bin/pytest                                  # run tests
```

### Host venv (Windows)

> Windows is supported but not the primary dev target. Use the
> **containerized path above** unless you have a specific reason to run
> on Windows directly — path-separator semantics in some resolvers can
> produce divergent results.

PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\pip.exe install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\pip.exe install -e .
Copy-Item .env.example .env
docker compose up -d neo4j
.venv\Scripts\pytest.exe
```

cmd.exe:

```cmd
py -3 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\pip install -e .
copy .env.example .env
docker compose up -d neo4j
.venv\Scripts\pytest
```

> **Path conventions below**: examples use `.venv/bin/<tool>` (host venv on Linux/macOS). Inside the dev container the tools are on `PATH` — drop the prefix entirely. On Windows host substitute `.venv\Scripts\<tool>.exe`, or activate the venv first.

Tests that hit external services (Datadog, GitHub, Neo4j) are marked `@pytest.mark.integration` and are skipped unless credentials are set.

## How to run

Two CLIs ship in the venv: `sg-ingest` (read sources → JSON in `./out/`)
and `sg-graph` (manage Neo4j: init schema, load JSON, query). Both use
settings from `.env`. Replace the `< >` placeholders below.

**Command prefixes by environment:**

| Environment | How to invoke |
|---|---|
| Container (recommended) | `docker compose --profile dev run --rm dev <cmd>` (or `podman compose ...`) — both CLIs on `PATH` |
| Host venv (Linux/macOS) | `.venv/bin/<tool>` |
| Host venv (Windows PS) | `.venv\Scripts\<tool>.exe` |

Examples below use `.venv/bin/<tool>`; translate as needed.

### 1. Ingest one or more repos

The recommended workflow is **clone from GitHub** — register a URL once,
ingest as many times as you like. Clones live in `./out/github_repos/`
and re-ingest is keyed on the commit SHA, so unchanged repos are
skipped automatically.

```bash
# Register a public repo (no token needed).
.venv/bin/sg-ingest github add fastapi/fastapi
.venv/bin/sg-ingest github add https://github.com/encode/httpx

# List + status.
.venv/bin/sg-ingest github list
.venv/bin/sg-ingest github status

# Clone-if-needed and ingest into ./out/.
.venv/bin/sg-ingest github ingest --all

# Re-running with no upstream changes is a no-op (SHA unchanged → skip).
.venv/bin/sg-ingest github ingest --all

# Remove a repo (default: also deletes the clone).
.venv/bin/sg-ingest github remove encode/httpx
# Or just wipe the clone, keep the DB row (next ingest re-clones).
.venv/bin/sg-ingest github clean fastapi/fastapi
.venv/bin/sg-ingest github clean --all
```

Private repos: set `GITHUB_TOKEN` in `.env`; the token is injected into
the clone URL during `git fetch` and scrubbed from `.git/config`
afterwards, so it's never persisted on disk. Validate the token works
*before* you queue private repos:

```bash
.venv/bin/sg-ingest github auth check
#   logged in as kevin-s on github.com
#     api:    https://api.github.com
#     scopes: repo, read:org
```

If the clone fails with `repository not found`, the CLI prints a doctor
message naming the exact env var to set (e.g. `set GITHUB_TOKEN and
retry`) — you don't have to guess whether the URL is wrong or the token
is missing.

#### Multi-host (GitHub Enterprise + github.com)

One PAT per host. The lookup convention is `GITHUB_TOKEN_<HOST>` where
`<HOST>` uppercases the hostname and replaces `.`/`-` with `_`:

```bash
# .env
GITHUB_TOKEN=ghp_for_github_com
GITHUB_TOKEN_GHE_ACME_COM=ghp_for_acme_ghe   # → host ghe.acme.com
GITHUB_TOKEN_GIT_INTERNAL=ghp_for_internal   # → host git.internal
```

Confirm each one independently:

```bash
.venv/bin/sg-ingest github auth check                          # github.com (default)
.venv/bin/sg-ingest github auth check --host ghe.acme.com      # GHE
.venv/bin/sg-ingest github auth show                           # table of all known hosts
```

`auth show` lists every host you have registered repos for plus
github.com, and tells you which env var to set for any that are
unconfigured.

Other knobs:

```ini
GITHUB_TOKEN=ghp_...                       # optional; required for private repos on github.com
GITHUB_TOKEN_<HOST>=ghp_...                # per-host PATs for GHE / other hosts
GITHUB_CLONES_DIR=./out/github_repos       # where shallow clones live
GITHUB_STORE_PATH=./out/github.db          # SQLite metadata (SHA cache)
GITHUB_DEFAULT_BRANCH=                     # leave empty = follow origin/HEAD
GITHUB_REPOS=owner/a,owner/b               # optional seed list; auto-registers on first run
```

#### Local-folder workflow (for fixtures + offline)

You can still point `testparser` at a local directory — useful for the
bundled `./data/` fixture and for repos you've cloned yourself:

* If the path contains repo markers (`.git`, `pyproject.toml`,
  `setup.py`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`,
  `build.gradle`) at the top level, **that path IS the service**. Its
  `repo_id` is the folder's own name.
* Otherwise its immediate subdirectories are scanned as separate repos
  (useful for monorepo-style layouts and the bundled `./data/` fixture).

Override the auto-detect with `TESTPARSER_SINGLE_REPO=true|false` if
the heuristic guesses wrong.

**Linux / macOS:**
```bash
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

The project has a `pyproject.toml` at its root, so auto-detect treats
it as a single repo named after the directory.

```bash
# Linux/macOS
TESTPARSER_ROOT="$(pwd)" .venv/bin/sg-ingest --out ./out --skip datadog --skip github
.venv/bin/sg-graph clear --yes && .venv/bin/sg-graph load --from ./out
```

```powershell
# Windows PowerShell
$env:TESTPARSER_ROOT = "$pwd"
.venv\Scripts\sg-ingest.exe --out .\out --skip datadog --skip github
.venv\Scripts\sg-graph.exe clear --yes
.venv\Scripts\sg-graph.exe load --from .\out
```

## Product UI (Explorer + Pipelines)

The repo ships a React + FastAPI product layer for visually exploring the
impact graph and generating Markdown reports. The CLI remains the source
of truth for ingestion — the UI is read-only and renders state straight
out of Neo4j and the adapter SQLite stores.

```bash
# One-time: install npm deps.
make ui-install

# Two terminals: API on :8000, UI on :5173.
make api-dev   # uvicorn api.main:app --reload
make ui-dev    # cd ui && npm run dev
```

Or containerized:

```bash
make product-up   # neo4j + api + ui via docker compose --profile product
# open http://localhost:5173
make product-down
```

**Pages:**

* **Explorer (`/explore`)** — search for any node, see its 1-hop
  neighborhood on the graph canvas, switch to "Impact" mode to walk
  downstream/upstream dependencies up to N hops, scan the affected list
  in the side panel, and click "Download report" to grab a Markdown
  summary.
* **Pipelines (`/pipelines`)** — one card per adapter (GitHub, Datadog,
  TestParser) showing last-run state pulled from `out/github.db` and
  `out/datadog.db`. To trigger a run, use `sg-ingest` in the terminal.

The API itself is reachable for scripting at <http://localhost:8000/docs>
(OpenAPI / Swagger UI).

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
