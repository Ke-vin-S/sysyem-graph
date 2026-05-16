# Multi-Repo Impact Analysis System - Monorepo Structure

```
impact-analysis-system/
├── README.md                          # Project overview
├── LICENSE
├── CONTRIBUTING.md                    # Contribution guidelines
├── .gitignore
├── Makefile                           # Build/test/deploy commands
├── docker-compose.yml                 # Local dev environment
├── .github/
│   └── workflows/
│       ├── tests.yml                  # Run tests on PR
│       ├── lint.yml                   # Code quality checks
│       └── release.yml                # Deploy on merge to main
│
├── docs/                              # Project documentation
│   ├── ARCHITECTURE.md                # System design (reference to complete_system_design.md)
│   ├── SETUP.md                       # Getting started
│   ├── API.md                         # API endpoints documentation
│   ├── ADAPTERS.md                    # How to write custom adapters
│   ├── CONTRIBUTING.md                # Development guide
│   ├── PHASES.md                      # 6-phase implementation roadmap
│   └── TROUBLESHOOTING.md
│
├── core/                              # Core system (shared, language-agnostic)
│   ├── README.md
│   ├── go.mod                         # Go module (core is in Go for performance)
│   ├── go.sum
│   ├── main.go
│   │
│   ├── types/                         # Shared data structures
│   │   ├── service.go                 # Service, ExternalConnection, CodeArtifact, TestCase
│   │   ├── change.go                  # Change, ImpactAnalysisResult
│   │   └── errors.go
│   │
│   ├── adapters/                      # Adapter interface + base implementations
│   │   ├── adapter.go                 # IngestionAdapter interface
│   │   ├── registry.go                # AdapterRegistry (manages all adapters)
│   │   ├── validator.go               # Validation logic
│   │   ├── merger.go                  # Merge results from multiple adapters
│   │   └── feedback/
│   │       ├── feedback_loop.go       # Auto-improvement from CI results
│   │       └── confidence_scorer.go   # Adjust confidence based on accuracy
│   │
│   ├── graph/                         # Neo4j interaction
│   │   ├── client.go                  # Neo4j client wrapper
│   │   ├── schema.go                  # Schema definition, indexes, constraints
│   │   ├── loader.go                  # Bulk load data into Neo4j
│   │   ├── queries/
│   │   │   ├── direct_impact.cypher   # Find direct dependencies
│   │   │   ├── transitive_impact.cypher
│   │   │   ├── schema_impact.cypher
│   │   │   ├── reverse_dependency.cypher
│   │   │   └── circular_deps.cypher
│   │   └── migrations/                # Schema versioning
│   │       ├── 001_initial_schema.cypher
│   │       ├── 002_add_indexes.cypher
│   │       └── migrations.go
│   │
│   ├── rules/                         # Impact analysis rules (Phase 3)
│   │   ├── rules.go                   # Rules interface
│   │   ├── direct_impact.go           # Rule 1
│   │   ├── transitive_impact.go       # Rule 2
│   │   ├── schema_impact.go           # Rule 3
│   │   ├── reverse_dependency.go      # Rule 4
│   │   └── engine.go                  # Impact analysis engine
│   │
│   ├── testselection/                 # Test selection (Phase 4)
│   │   ├── classifier.go              # Auto-detect UNIT/COMPONENT/INTEGRATION
│   │   ├── selector.go                # Select minimal test set
│   │   ├── pyramid.go                 # Test pyramid (tier by type)
│   │   └── heuristics.go              # Classification heuristics
│   │
│   ├── config/                        # Configuration management
│   │   ├── config.go                  # Load config from YAML/env
│   │   ├── validation.go              # Validate config
│   │   └── defaults.go                # Default values
│   │
│   └── test/                          # Unit tests for core
│       ├── adapters_test.go
│       ├── graph_test.go
│       ├── rules_test.go
│       └── testselection_test.go
│
├── ingestion/                         # Data ingestion (Phase 1)
│   ├── README.md
│   ├── go.mod
│   │
│   ├── adapters/                      # Built-in adapters
│   │   │
│   │   ├── datadog/
│   │   │   ├── adapter.go             # DatadogAdapter
│   │   │   ├── client.go              # Datadog API client
│   │   │   ├── trace_parser.go        # Parse APM traces
│   │   │   ├── config.go              # Datadog-specific config
│   │   │   └── test/
│   │   │       └── adapter_test.go
│   │   │
│   │   ├── github/
│   │   │   ├── adapter.go             # GitHubCodeAdapter
│   │   │   ├── parser.go              # AST parser (delegates to language-specific)
│   │   │   ├── artifact_extractor.go  # Extract functions, endpoints, schemas
│   │   │   ├── config.go
│   │   │   └── test/
│   │   │
│   │   ├── testparser/
│   │   │   ├── adapter.go             # TestParserAdapter
│   │   │   ├── classifier.go          # Classify UNIT/COMPONENT/INTEGRATION
│   │   │   ├── coverage.go            # Extract coverage info
│   │   │   ├── config.go
│   │   │   └── test/
│   │   │
│   │   ├── documentation/
│   │   │   ├── adapter.go             # DocumentationAdapter
│   │   │   ├── parser.go              # Parse Markdown, extract connections
│   │   │   ├── nlp.go                 # Natural language processing (find "A calls B")
│   │   │   ├── config.go
│   │   │   └── test/
│   │   │
│   │   ├── openapi/
│   │   │   ├── adapter.go             # OpenAPI/Swagger parser
│   │   │   ├── schema_extractor.go
│   │   │   ├── config.go
│   │   │   └── test/
│   │   │
│   │   └── protobuf/
│   │       ├── adapter.go             # Protobuf schema parser
│   │       ├── schema_extractor.go
│   │       ├── config.go
│   │       └── test/
│   │
│   ├── parsers/                       # Language-specific parsers (pluggable)
│   │   ├── parser.go                  # Parser interface
│   │   ├── go/
│   │   │   ├── parser.go
│   │   │   ├── ast.go
│   │   │   └── test/
│   │   ├── python/
│   │   │   ├── parser.go
│   │   │   ├── ast.go
│   │   │   └── test/
│   │   ├── java/
│   │   │   ├── parser.go
│   │   │   ├── ast.go
│   │   │   └── test/
│   │   └── typescript/
│   │       ├── parser.go
│   │       ├── ast.go
│   │       └── test/
│   │
│   ├── config.yaml                    # Ingestion configuration
│   ├── examples/                      # Example configs
│   │   ├── datadog.yaml
│   │   ├── github.yaml
│   │   └── full.yaml
│   │
│   └── test/
│       └── integration_test.go        # Full pipeline tests
│
├── api/                               # REST API server (Phase 5)
│   ├── README.md
│   ├── go.mod
│   ├── main.go
│   │
│   ├── handlers/
│   │   ├── analyze.go                 # POST /api/analyze
│   │   ├── stats.go                   # GET /api/stats
│   │   ├── graph.go                   # GET /api/graph/{serviceId}
│   │   ├── adapters.go                # GET /api/adapters (list registered adapters)
│   │   ├── health.go                  # GET /health
│   │   └── middleware.go              # Auth, logging, error handling
│   │
│   ├── models/
│   │   ├── request.go                 # API request/response types
│   │   └── response.go
│   │
│   ├── config/
│   │   └── api.yaml                   # API-specific config
│   │
│   └── test/
│       ├── handlers_test.go
│       └── integration_test.go
│
├── webhook/                           # Git webhook listener
│   ├── README.md
│   ├── go.mod
│   ├── main.go
│   │
│   ├── providers/                     # Git provider adapters
│   │   ├── github/
│   │   │   ├── handler.go
│   │   │   └── parser.go              # Parse GitHub webhook payload
│   │   ├── gitlab/
│   │   │   ├── handler.go
│   │   │   └── parser.go
│   │   ├── gitea/
│   │   │   ├── handler.go
│   │   │   └── parser.go
│   │   └── webhook.go                 # Common webhook interface
│   │
│   ├── detector/
│   │   ├── change_detector.go         # Detect change type (CODE, SCHEMA, API)
│   │   └── affected_files.go          # Parse changed files
│   │
│   └── test/
│       └── webhook_test.go
│
├── dashboard/                         # Web UI (Phase 6)
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
│   │   │   ├── ServiceGraph.tsx       # Neo4j visualization (interactive)
│   │   │   ├── ImpactChains.tsx       # Display A→B→C chains
│   │   │   ├── TestCoverage.tsx       # Heatmap of test coverage
│   │   │   ├── CIDashboard.tsx        # CI time trends
│   │   │   ├── Alerts.tsx             # Stale connections, circular deps
│   │   │   └── Metrics.tsx            # False pos/neg rates
│   │   │
│   │   ├── pages/
│   │   │   ├── HomePage.tsx
│   │   │   ├── GraphPage.tsx
│   │   │   ├── AlertsPage.tsx
│   │   │   └── SettingsPage.tsx
│   │   │
│   │   ├── api/
│   │   │   └── client.ts              # API client (fetch wrapper)
│   │   │
│   │   └── utils/
│   │       ├── neo4j-viz.ts           # Neo4j visualization library
│   │       └── formatters.ts
│   │
│   └── test/
│       └── components_test.tsx
│
├── cli/                               # Command-line tool
│   ├── README.md
│   ├── go.mod
│   ├── main.go
│   │
│   ├── cmd/
│   │   ├── root.go
│   │   ├── ingest.go                  # ingest subcommand
│   │   ├── analyze.go                 # analyze subcommand
│   │   ├── select-tests.go            # select-tests subcommand
│   │   ├── graph.go                   # graph subcommand (query Neo4j)
│   │   ├── adapter.go                 # adapter subcommand (list, enable, disable)
│   │   └── config.go                  # config subcommand
│   │
│   └── test/
│       └── cli_test.go
│
├── observability/                     # Monitoring & alerts (Phase 6)
│   ├── README.md
│   │
│   ├── metrics/
│   │   ├── prometheus/
│   │   │   ├── metrics.go             # Prometheus client
│   │   │   └── collectors.go          # Custom collectors
│   │   └── statsd/
│   │       └── client.go
│   │
│   ├── alerts/
│   │   ├── alerter.go                 # Alert manager
│   │   ├── rules.go                   # Alert rules (Alertmanager config)
│   │   ├── slack.go                   # Slack notifier
│   │   ├── pagerduty.go               # PagerDuty integration
│   │   └── email.go                   # Email notifier
│   │
│   ├── dashboards/
│   │   ├── grafana/
│   │   │   ├── service-dependencies.json
│   │   │   ├── ci-pipeline.json
│   │   │   ├── test-coverage.json
│   │   │   └── false-pos-neg.json
│   │   └── custom/
│   │       └── react dashboard (in dashboard/)
│   │
│   └── config.yaml                    # Observability config
│
├── ci/                                # CI/CD integration
│   ├── github-actions/
│   │   ├── analyze-impact.yml         # Reusable workflow
│   │   └── run-selected-tests.yml
│   │
│   ├── jenkins/
│   │   ├── Jenkinsfile
│   │   └── shared-library/
│   │
│   ├── gitlab/
│   │   └── .gitlab-ci.yml
│   │
│   └── templates/
│       └── .github/workflows/          # Template for user projects
│           └── smart-tests.yml
│
├── examples/                          # Example implementations
│   ├── custom-slack-adapter/
│   │   ├── main.go
│   │   └── README.md
│   │
│   ├── custom-kubernetes-adapter/
│   │   ├── main.go
│   │   └── README.md
│   │
│   ├── test-project/                  # Test polyrepo setup
│   │   ├── auth-service/
│   │   │   ├── main.go
│   │   │   ├── go.mod
│   │   │   └── test_*.go
│   │   ├── payment-service/
│   │   │   ├── main.py
│   │   │   ├── requirements.txt
│   │   │   └── test_*.py
│   │   └── order-service/
│   │       ├── main.go
│   │       ├── go.mod
│   │       └── test_*.go
│   │
│   └── docker-compose-example.yml
│
├── tests/                             # Integration & E2E tests
│   ├── integration/
│   │   ├── full_pipeline_test.go      # Full system test (ingestion → graph → impact → tests)
│   │   ├── adapter_integration_test.go
│   │   └── neo4j_integration_test.go
│   │
│   ├── e2e/
│   │   ├── polyrepo_scenario_test.go  # End-to-end test with example repos
│   │   └── api_e2e_test.go
│   │
│   └── fixtures/
│       ├── sample_repos/
│       ├── sample_traces.json
│       ├── sample_tests.json
│       └── sample_code.json
│
├── scripts/                           # Utility scripts
│   ├── setup.sh                       # Initialize system
│   ├── start-dev.sh                   # Start local dev environment
│   ├── generate-docs.sh               # Generate API docs from code
│   ├── run-tests.sh                   # Run all tests
│   ├── load-sample-data.sh            # Load sample data into Neo4j
│   ├── migrate-neo4j.sh               # Run schema migrations
│   └── deploy.sh                      # Deploy to production
│
├── config/                            # Global configuration
│   ├── config.yaml                    # Main config file
│   ├── config.example.yaml            # Example config
│   ├── docker-compose.yml             # Local dev (Neo4j, API, etc)
│   └── env/
│       ├── .env.example
│       ├── .env.local                 # Local overrides
│       ├── .env.staging
│       └── .env.prod
│
├── docker/                            # Docker images
│   ├── Dockerfile.api                 # API server
│   ├── Dockerfile.webhook             # Webhook listener
│   ├── Dockerfile.cli                 # CLI tool
│   ├── Dockerfile.dashboard           # Web UI
│   └── Dockerfile.all                 # All-in-one image
│
├── helm/                              # Kubernetes deployment
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
├── deployment/                        # Deployment guides
│   ├── DEPLOYMENT.md
│   ├── LOCAL_SETUP.md
│   ├── DOCKER_SETUP.md
│   ├── KUBERNETES_SETUP.md
│   └── AWS_SETUP.md
│
└── Makefile                           # Build targets
    # make install           - Install dependencies
    # make build            - Build all services
    # make test             - Run all tests
    # make lint             - Run linters
    # make fmt              - Format code
    # make docker-build     - Build Docker images
    # make docker-push      - Push to registry
    # make k8s-deploy       - Deploy to Kubernetes
    # make dev              - Start local dev env
    # make clean            - Clean build artifacts
```

---

## Module Breakdown

### **Core** (Language: Go)
- **Purpose**: Shared logic (types, adapter registry, Neo4j interaction, rules, test selection)
- **Exports**: Library for other modules to import
- **Zero dependencies on other modules** (api, webhook, cli all depend on core)

### **Ingestion** (Language: Go)
- **Purpose**: Data extraction (Datadog, GitHub, tests, docs)
- **Adapters**: Pluggable (built-in + custom)
- **Language parsers**: Delegates to language-specific AST tools
- **Output**: CSV/JSON ready for Neo4j load

### **API** (Language: Go)
- **Purpose**: REST endpoints for impact analysis
- **Endpoints**:
  - `POST /api/analyze` → Call impact engine + test selector
  - `GET /api/stats` → System health metrics
  - `GET /api/graph/{serviceId}` → Dependency visualization
- **Dependencies**: core, Neo4j driver

### **Webhook** (Language: Go)
- **Purpose**: Listen for Git events, trigger impact analysis
- **Providers**: GitHub, GitLab, Gitea, Bitbucket
- **Flow**: Webhook → Detect change → Call API /analyze
- **Dependencies**: core, API client

### **Dashboard** (Language: TypeScript/React)
- **Purpose**: Web UI for visualization
- **Pages**: Graph view, impact chains, alerts, CI trends
- **Dependencies**: API client, Neo4j viz library (Neovis.js)

### **CLI** (Language: Go)
- **Purpose**: Command-line tool for operations
- **Commands**: `ingest`, `analyze`, `select-tests`, `graph`, `adapter`, `config`
- **Use cases**: Manual queries, debugging, admin tasks
- **Dependencies**: core, Neo4j driver

### **Observability** (Language: Go + YAML)
- **Purpose**: Monitoring, alerting, dashboards
- **Metrics**: Prometheus (flakiness, false pos/neg, CI time)
- **Alerts**: Alertmanager rules (Slack, email, PagerDuty)
- **Dashboards**: Grafana JSON files

### **CI/CD** (Language: YAML)
- **Purpose**: GitHub Actions, Jenkins, GitLab CI templates
- **Integrations**: Webhook listener, impact analysis API

### **Tests** (Language: Go)
- **Integration tests**: Full pipeline (ingestion → graph → impact)
- **E2E tests**: Real example repos
- **Fixtures**: Sample data, repos

---

## Dependencies Graph

```
        ┌─────────────────────────────┐
        │       Core (shared)          │
        │  • Types                     │
        │  • Adapter Registry          │
        │  • Neo4j Client              │
        │  • Impact Rules              │
        │  • Test Selection            │
        └──────────────┬────────────────┘
                       │
        ┌──────────────┼──────────────────────┬──────────────┐
        │              │                      │              │
        ▼              ▼                      ▼              ▼
   ┌────────┐    ┌─────────┐          ┌──────────┐    ┌─────────┐
   │ API    │    │Webhook  │          │   CLI    │    │Ingestion│
   │(Go)    │    │(Go)     │          │  (Go)    │    │  (Go)   │
   └────────┘    └─────────┘          └──────────┘    └─────────┘
        │              │                      │              │
        └──────────────┼──────────────────────┴──────────────┘
                       │
                       ▼
              ┌──────────────────┐
              │   Neo4j (DB)     │
              └──────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
   ┌─────────┐   ┌──────────┐   ┌──────────┐
   │ Dashboard│   │Observ.  │   │CI/CD     │
   │(React)   │   │(Prom+   │   │(YAML)    │
   │          │   │ Grafana)│   │          │
   └──────────┘   └─────────┘   └──────────┘
```

---

## Build & Run

### **Local Development**
```bash
make dev                    # Start all services locally (Docker Compose)
# Services:
#   - Neo4j (localhost:7687)
#   - API (localhost:8080)
#   - Dashboard (localhost:3000)
#   - Webhook (localhost:9000)
```

### **Build**
```bash
make build                  # Build all binaries
make docker-build           # Build all Docker images
make docker-push            # Push to registry
```

### **Deploy**
```bash
make k8s-deploy            # Deploy to Kubernetes (Helm)
# Sets up:
#   - API deployment
#   - Webhook deployment
#   - Neo4j StatefulSet
#   - Dashboard deployment
#   - Prometheus/Grafana (optional)
```

### **Test**
```bash
make test                   # Run all tests
make test-integration       # Integration tests only
make test-e2e              # E2E tests only
```

---

## Entry Points

| Role | Entry Point | Command |
|------|-------------|---------|
| **Developer** | Dashboard | `http://localhost:3000` |
| **Operator** | CLI | `./impact-cli analyze --repo auth-service` |
| **Automation** | API | `POST /api/analyze` |
| **Git Integration** | Webhook | `POST /webhook/github` |
| **Monitoring** | Grafana | `http://localhost:3000/grafana` |

---

## Adding a New Adapter

1. Create `ingestion/adapters/new-adapter/`
2. Implement `IngestionAdapter` interface
3. Register in `registry` (YAML config or code)
4. No changes to core system needed

---

## Environment Variables

```bash
# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password

# Datadog
DATADOG_API_KEY=...
DATADOG_API_URL=https://api.datadoghq.com

# GitHub
GITHUB_TOKEN=...
GITHUB_ORG=mycompany

# API
API_PORT=8080
API_LOG_LEVEL=info

# Webhook
WEBHOOK_PORT=9000
WEBHOOK_SECRET=...

# Dashboard
REACT_APP_API_URL=http://localhost:8080
```

---

This is a **production-ready monorepo structure** that:
- ✅ Separates concerns (core, adapters, API, UI)
- ✅ Allows independent scaling (each module can be deployed separately)
- ✅ Supports extensibility (adapters are plugins)
- ✅ Has CI/CD ready (Dockerfile, Helm, GitHub Actions)
- ✅ Includes observability (Prometheus, Grafana, alerts)
- ✅ Scales from single machine → Kubernetes
