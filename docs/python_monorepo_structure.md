# Python Monorepo Structure - Ready for Implementation

```
impact-analysis-system/
│
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── .gitignore
├── Makefile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
│
├── .github/
│   └── workflows/
│       ├── tests.yml
│       ├── lint.yml
│       └── release.yml
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SETUP.md
│   ├── API.md
│   ├── ADAPTERS.md
│   ├── PHASES.md
│   ├── CONTRIBUTING.md
│   └── TROUBLESHOOTING.md
│
├── core/
│   ├── __init__.py
│   ├── pyproject.toml
│   │
│   ├── types/
│   │   ├── __init__.py
│   │   ├── service.py                 # Service, ExternalConnection, CodeArtifact, TestCase
│   │   ├── change.py                  # Change, ImpactAnalysisResult
│   │   └── errors.py
│   │
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py                    # IngestionAdapter base class (abstract)
│   │   ├── registry.py                # AdapterRegistry (orchestrates all adapters)
│   │   ├── validator.py               # Validation logic
│   │   ├── merger.py                  # Merge results from multiple adapters
│   │   ├── mapper.py                  # Map Git artifacts to Datadog connections
│   │   ├── confidence_scorer.py       # Confidence scoring
│   │   └── feedback/
│   │       ├── __init__.py
│   │       ├── feedback_loop.py       # Auto-improvement from CI results
│   │       └── confidence_updater.py
│   │
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── client.py                  # Neo4j client wrapper
│   │   ├── schema.py                  # Schema definition, constraints
│   │   ├── loader.py                  # Bulk load data into Neo4j
│   │   ├── queries/
│   │   │   ├── __init__.py
│   │   │   ├── direct_impact.py       # Query: direct dependencies
│   │   │   ├── transitive_impact.py   # Query: A→B→C chains
│   │   │   ├── schema_impact.py
│   │   │   ├── reverse_dependency.py
│   │   │   ├── circular_deps.py
│   │   │   └── query_builder.py       # Build Cypher queries
│   │   └── migrations/
│   │       ├── __init__.py
│   │       ├── 001_initial_schema.py
│   │       ├── 002_add_indexes.py
│   │       └── migrator.py
│   │
│   ├── rules/
│   │   ├── __init__.py
│   │   ├── rule.py                    # Rule interface
│   │   ├── direct_impact.py           # Rule 1
│   │   ├── transitive_impact.py       # Rule 2
│   │   ├── schema_impact.py           # Rule 3
│   │   ├── reverse_dependency.py      # Rule 4
│   │   └── engine.py                  # Impact analysis engine
│   │
│   ├── testselection/
│   │   ├── __init__.py
│   │   ├── classifier.py              # Auto-detect UNIT/COMPONENT/INTEGRATION
│   │   ├── selector.py                # Select minimal test set
│   │   ├── pyramid.py                 # Test pyramid (tier by type)
│   │   └── heuristics.py              # Classification heuristics
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py                # Pydantic BaseSettings
│   │   ├── validation.py              # Validate config
│   │   └── defaults.py                # Default values
│   │
│   └── tests/
│       ├── __init__.py
│       ├── test_adapters.py
│       ├── test_graph.py
│       ├── test_rules.py
│       ├── test_testselection.py
│       └── conftest.py                # Pytest fixtures
│
├── ingestion/
│   ├── __init__.py
│   ├── pyproject.toml
│   │
│   ├── adapters/
│   │   ├── __init__.py
│   │   │
│   │   ├── datadog/
│   │   │   ├── __init__.py
│   │   │   ├── adapter.py             # DatadogAdapter
│   │   │   ├── client.py              # Datadog API client
│   │   │   ├── trace_parser.py        # Parse APM traces
│   │   │   ├── config.py
│   │   │   └── tests/
│   │   │       ├── __init__.py
│   │   │       └── test_adapter.py
│   │   │
│   │   ├── github/
│   │   │   ├── __init__.py
│   │   │   ├── adapter.py             # GitHubAdapter
│   │   │   ├── client.py              # GitHub API client
│   │   │   ├── repo_fetcher.py        # Fetch files from repo
│   │   │   ├── config.py
│   │   │   └── tests/
│   │   │       └── test_adapter.py
│   │   │
│   │   ├── testparser/
│   │   │   ├── __init__.py
│   │   │   ├── adapter.py             # TestParserAdapter
│   │   │   ├── classifier.py          # Classify UNIT/COMPONENT/INTEGRATION
│   │   │   ├── coverage.py            # Extract coverage info
│   │   │   ├── config.py
│   │   │   └── tests/
│   │   │       └── test_adapter.py
│   │   │
│   │   ├── documentation/
│   │   │   ├── __init__.py
│   │   │   ├── adapter.py             # DocumentationAdapter
│   │   │   ├── parser.py              # Parse Markdown
│   │   │   ├── nlp.py                 # Natural language extraction
│   │   │   ├── config.py
│   │   │   └── tests/
│   │   │       └── test_adapter.py
│   │   │
│   │   ├── openapi/
│   │   │   ├── __init__.py
│   │   │   ├── adapter.py
│   │   │   ├── schema_extractor.py
│   │   │   ├── config.py
│   │   │   └── tests/
│   │   │
│   │   └── protobuf/
│   │       ├── __init__.py
│   │       ├── adapter.py
│   │       ├── schema_extractor.py
│   │       ├── config.py
│   │       └── tests/
│   │
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── parser.py                  # Parser interface
│   │   ├── python_parser.py
│   │   ├── go_parser.py
│   │   ├── java_parser.py
│   │   ├── typescript_parser.py
│   │   └── tests/
│   │
│   ├── config.example.yaml
│   ├── config.yaml
│   └── tests/
│       └── test_integration.py
│
├── api/
│   ├── __init__.py
│   ├── pyproject.toml
│   ├── main.py
│   │
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── analyze.py                 # POST /api/analyze
│   │   ├── stats.py                   # GET /api/stats
│   │   ├── graph.py                   # GET /api/graph/{serviceId}
│   │   ├── adapters.py                # GET /api/adapters
│   │   ├── health.py                  # GET /health
│   │   └── middleware.py              # Auth, logging, error handling
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── request.py                 # Request models
│   │   └── response.py                # Response models
│   │
│   ├── config/
│   │   └── api.yaml
│   │
│   └── tests/
│       ├── __init__.py
│       ├── test_handlers.py
│       └── test_integration.py
│
├── webhook/
│   ├── __init__.py
│   ├── pyproject.toml
│   ├── main.py
│   │
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── github/
│   │   │   ├── __init__.py
│   │   │   ├── handler.py
│   │   │   └── parser.py
│   │   ├── gitlab/
│   │   │   ├── __init__.py
│   │   │   ├── handler.py
│   │   │   └── parser.py
│   │   ├── gitea/
│   │   │   ├── __init__.py
│   │   │   ├── handler.py
│   │   │   └── parser.py
│   │   └── base.py                    # Common webhook interface
│   │
│   ├── detector/
│   │   ├── __init__.py
│   │   ├── change_detector.py
│   │   └── affected_files.py
│   │
│   └── tests/
│       ├── __init__.py
│       └── test_webhook.py
│
├── dashboard/
│   ├── README.md
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   │
│   ├── public/
│   │   └── index.html
│   │
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── index.css
│   │   │
│   │   ├── components/
│   │   │   ├── ServiceGraph.tsx
│   │   │   ├── ImpactChains.tsx
│   │   │   ├── TestCoverage.tsx
│   │   │   ├── CIDashboard.tsx
│   │   │   ├── Alerts.tsx
│   │   │   └── Metrics.tsx
│   │   │
│   │   ├── pages/
│   │   │   ├── HomePage.tsx
│   │   │   ├── GraphPage.tsx
│   │   │   ├── AlertsPage.tsx
│   │   │   └── SettingsPage.tsx
│   │   │
│   │   ├── api/
│   │   │   └── client.ts
│   │   │
│   │   └── utils/
│   │       ├── neo4j-viz.ts
│   │       └── formatters.ts
│   │
│   └── tests/
│       └── components.test.tsx
│
├── cli/
│   ├── __init__.py
│   ├── pyproject.toml
│   ├── main.py
│   │
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── ingest.py
│   │   ├── analyze.py
│   │   ├── select_tests.py
│   │   ├── graph.py
│   │   ├── adapter.py
│   │   └── config.py
│   │
│   └── tests/
│       └── test_cli.py
│
├── observability/
│   ├── __init__.py
│   ├── pyproject.toml
│   │
│   ├── metrics/
│   │   ├── __init__.py
│   │   ├── prometheus.py
│   │   └── collectors.py
│   │
│   ├── alerts/
│   │   ├── __init__.py
│   │   ├── alerter.py
│   │   ├── slack.py
│   │   ├── email.py
│   │   └── pagerduty.py
│   │
│   ├── dashboards/
│   │   ├── grafana/
│   │   │   ├── service-dependencies.json
│   │   │   ├── ci-pipeline.json
│   │   │   ├── test-coverage.json
│   │   │   └── false-pos-neg.json
│   │
│   └── config.yaml
│
├── ci/
│   ├── github-actions/
│   │   ├── analyze-impact.yml
│   │   └── run-selected-tests.yml
│   │
│   ├── templates/
│   │   └── .github/workflows/
│   │       └── smart-tests.yml
│   │
│   └── README.md
│
├── examples/
│   ├── custom_slack_adapter/
│   │   ├── __init__.py
│   │   ├── adapter.py
│   │   ├── config.yaml
│   │   └── README.md
│   │
│   ├── custom_kubernetes_adapter/
│   │   ├── __init__.py
│   │   ├── adapter.py
│   │   ├── config.yaml
│   │   └── README.md
│   │
│   └── test_project/
│       ├── auth-service/
│       │   ├── main.py
│       │   ├── requirements.txt
│       │   ├── tests/
│       │   │   ├── test_*.py
│       │   └── src/
│       │       └── auth.py
│       │
│       ├── payment-service/
│       │   ├── main.go
│       │   ├── go.mod
│       │   └── test_*.go
│       │
│       └── order-service/
│           ├── main.py
│           ├── requirements.txt
│           └── tests/
│
├── tests/
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── test_full_pipeline.py
│   │   ├── test_adapter_integration.py
│   │   └── test_neo4j_integration.py
│   │
│   ├── e2e/
│   │   ├── __init__.py
│   │   ├── test_polyrepo_scenario.py
│   │   └── test_api_e2e.py
│   │
│   └── fixtures/
│       ├── sample_repos.py
│       ├── sample_traces.json
│       ├── sample_tests.json
│       └── sample_code.json
│
├── scripts/
│   ├── setup.sh
│   ├── start-dev.sh
│   ├── generate-docs.sh
│   ├── run-tests.sh
│   ├── load-sample-data.sh
│   ├── migrate-neo4j.sh
│   └── deploy.sh
│
├── config/
│   ├── config.yaml
│   ├── config.example.yaml
│   ├── docker-compose.yml
│   └── env/
│       ├── .env.example
│       ├── .env.local
│       ├── .env.staging
│       └── .env.prod
│
├── docker/
│   ├── Dockerfile.api
│   ├── Dockerfile.webhook
│   ├── Dockerfile.cli
│   ├── Dockerfile.dashboard
│   └── Dockerfile.all
│
├── helm/
│   ├── Chart.yaml
│   ├── values.yaml
│   ├── values-dev.yaml
│   ├── values-prod.yaml
│   └── templates/
│       ├── api-deployment.yaml
│       ├── webhook-deployment.yaml
│       ├── neo4j-statefulset.yaml
│       ├── configmap.yaml
│       └── service.yaml
│
├── deployment/
│   ├── DEPLOYMENT.md
│   ├── LOCAL_SETUP.md
│   ├── DOCKER_SETUP.md
│   ├── KUBERNETES_SETUP.md
│   └── AWS_SETUP.md
│
└── Makefile
```

---

## **Files to Create in Each Module**

### **Core Module (`core/`)**

```
Files to create:

1. core/types/service.py
   - Service class
   - ExternalConnection class
   - CodeArtifact class
   - TestCase class
   - Change class
   - ImpactAnalysisResult class

2. core/types/errors.py
   - Custom exceptions

3. core/adapters/base.py
   - IngestionAdapter abstract class
   - Required methods: extract(), validate(), get_coverage()

4. core/adapters/registry.py
   - AdapterRegistry class
   - run_all() method (orchestrator)
   - register(), unregister(), enable(), disable()

5. core/adapters/merger.py
   - merge_results() function
   - Deduplication logic
   - Conflict resolution

6. core/adapters/mapper.py
   - Map Git artifacts to Datadog connections
   - match_endpoint() logic

7. core/adapters/validator.py
   - Validation functions
   - Check data integrity

8. core/adapters/confidence_scorer.py
   - Score confidence for each edge

9. core/graph/client.py
   - Neo4j driver wrapper

10. core/graph/schema.py
    - Define schema (constraints, indexes)

11. core/graph/loader.py
    - Load data into Neo4j

12. core/graph/queries/*.py
    - Cypher query builders

13. core/rules/engine.py
    - ImpactAnalysisEngine
    - Apply 4 rules

14. core/testselection/classifier.py
    - Auto-classify tests

15. core/testselection/selector.py
    - Select minimal tests

16. core/config/settings.py
    - Pydantic settings
```

### **Ingestion Module (`ingestion/`)**

```
Files to create:

1. ingestion/adapters/datadog/adapter.py
   - DatadogAdapter class

2. ingestion/adapters/datadog/client.py
   - Datadog API client

3. ingestion/adapters/datadog/trace_parser.py
   - Parse traces

4. ingestion/adapters/github/adapter.py
   - GitHubAdapter class

5. ingestion/adapters/github/repo_fetcher.py
   - Fetch files from GitHub

6. ingestion/adapters/testparser/adapter.py
   - TestParserAdapter class

7. ingestion/adapters/testparser/classifier.py
   - Classify tests

8. ingestion/adapters/documentation/adapter.py
   - DocumentationAdapter class

9. ingestion/adapters/documentation/parser.py
   - Parse Markdown

10. ingestion/parsers/python_parser.py
    - Parse Python AST

11. ingestion/parsers/go_parser.py
    - Parse Go AST

12. ingestion/parsers/java_parser.py
    - Parse Java AST

13. ingestion/parsers/typescript_parser.py
    - Parse TypeScript AST
```

### **API Module (`api/`)**

```
Files to create:

1. api/main.py
   - FastAPI app setup
   - Route registration

2. api/handlers/analyze.py
   - POST /api/analyze endpoint

3. api/handlers/stats.py
   - GET /api/stats endpoint

4. api/handlers/graph.py
   - GET /api/graph/{serviceId} endpoint

5. api/models/request.py
   - Pydantic request models

6. api/models/response.py
   - Pydantic response models
```

### **Webhook Module (`webhook/`)**

```
Files to create:

1. webhook/main.py
   - FastAPI app for webhooks

2. webhook/providers/github/handler.py
   - GitHub webhook handler

3. webhook/providers/gitlab/handler.py
   - GitLab webhook handler

4. webhook/detector/change_detector.py
   - Detect what changed
```

### **CLI Module (`cli/`)**

```
Files to create:

1. cli/main.py
   - Typer CLI app

2. cli/commands/ingest.py
   - ingest command

3. cli/commands/analyze.py
   - analyze command

4. cli/commands/select_tests.py
   - select-tests command

5. cli/commands/graph.py
   - graph command
```

---

## **Configuration Files**

```
1. pyproject.toml (root)
   - Project metadata
   - Dependencies (fastapi, neo4j, pydantic, typer, pytest, etc)

2. requirements.txt
   - List all dependencies

3. requirements-dev.txt
   - Dev dependencies (pytest, mypy, black, etc)

4. .env.example
   - Environment variable template

5. config.yaml (root and each module)
   - Configuration

6. docker-compose.yml
   - Neo4j, API, webhook services

7. Makefile
   - make install
   - make dev
   - make test
   - make lint
   - make build
```

---

## **Documentation Files**

```
1. docs/ARCHITECTURE.md
   - System overview

2. docs/SETUP.md
   - Getting started guide

3. docs/API.md
   - API endpoints

4. docs/ADAPTERS.md
   - How to write custom adapters

5. docs/PHASES.md
   - 6-phase roadmap

6. README.md
   - Project overview

7. CONTRIBUTING.md
   - Contribution guidelines
```

---

## **How to Use This Structure**

### **Step 1: Create Directory Structure**
```bash
mkdir -p impact-analysis-system
cd impact-analysis-system

# Paste the folder structure above, create all directories
```

### **Step 2: Create Empty Files**
```bash
touch core/__init__.py
touch core/types/__init__.py
touch core/types/service.py
# ... etc for all files
```

### **Step 3: Ask Claude Code**

For **each file**, ask Claude:

```
Create impact-analysis-system/core/types/service.py

This file should define:
- Service class (pydantic BaseModel)
  Fields: id, name, repoUrl, language, framework, owner
  
- ExternalConnection class
  Fields: id, type, sourceServiceId, targetServiceId, endpoint, frequency, criticality, etc
  
- CodeArtifact class
  Fields: id, repoId, type, name, file, externalConnections
  
- TestCase class
  Fields: id, repoId, type, name, file, affectedRepos
  
Use pydantic v2 BaseModel for all classes.
Include docstrings and type hints.
```

### **Step 4: Build Module by Module**

Order:
1. `core/types/` — Data structures
2. `core/adapters/` — Adapter framework
3. `core/graph/` — Neo4j interaction
4. `core/rules/` — Impact analysis
5. `core/testselection/` — Test selection
6. `ingestion/adapters/` — Data sources
7. `api/` — REST API
8. `webhook/` — Git integration
9. `cli/` — CLI tool
10. `dashboard/` — UI
11. `observability/` — Monitoring

---

## **Claude Code Prompts Template**

When asking Claude to build each file:

```
Create [full_path_to_file.py]

Context:
- This is part of impact-analysis-system (polyrepo impact analysis)
- We're using Python 3.10+, FastAPI, Pydantic v2, Neo4j, Typer, pytest

File purpose:
[Describe what this file does]

Required classes/functions:
[List what to implement]

Example usage:
[Show how this file is used]

Constraints:
- Use type hints throughout
- Include docstrings
- Follow PEP 8
- Make it testable
```

---

This structure is **ready to paste** and **ready for Claude Code** to fill in.

Just create the directories, then ask Claude Code to implement each file one by one.
