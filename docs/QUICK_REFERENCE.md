# Multi-Repo Impact Analysis System: Quick Reference
## Neo4j-Based Architecture for Polyrepo Test Selection

---

## What You're Building

A **smart CI system** that:
- Discovers inter-service dependencies via **Datadog traces** (real production behavior)
- Stores dependencies in **Neo4j graph** (queryable, transitive-aware)
- Detects **transitive impact** (A→B→C chains)
- Selects **minimal tests** to run on each change
- **Reduces CI time** from 5+ min → 1 min (80% reduction)

---

## The Problem You're Solving

**Current state** (polyrepo without smart testing):
```
Developer pushes to auth-service
  ↓
CI runs ALL tests in ALL repos (100+ tests)
  ↓
Takes 5–10 minutes
  ↓
Developer waits, context lost
  ↓
Merge blocked or delayed
```

**After implementation**:
```
Developer pushes to auth-service
  ↓
System detects: "auth-service calls payment-service, payment calls order-service"
  ↓
Runs only affected tests: auth unit tests + payment/order integration tests
  ↓
Takes 40 seconds
  ↓
Instant feedback
  ↓
Merge approved
```

---

## Core Insight: The Graph

```
Neo4j stores the dependency graph as relationships:

Service A ─(INITIATES)→ ExternalConnection ─(TARGETS)→ Service B
                              │
                              └─ HTTP POST /api/charges
                              └─ Frequency: 500 calls/min
                              └─ Criticality: CRITICAL

When A changes:
  • Query Neo4j: who calls A?
  • Find B, C, D (direct)
  • Find E, F, G (transitive: B→E, C→F, D→G)
  • Query: which tests cover B, C, D, E, F, G?
  • Run those tests
  • Done
```

---

## 6-Phase Implementation Plan

### Phase 1: Data Ingestion (Weeks 1–3)
**Goal**: Extract dependencies from production

**Three data sources**:

1. **Datadog APM Traces** (real service calls)
   - Query: Last 30 days of production spans
   - Extract: (source_service, target_service, endpoint, frequency, error_rate)
   - Store: ExternalConnection nodes
   - Tools: Datadog API client

2. **Code Static Analysis** (public APIs, functions, endpoints)
   - Parse: Each repo's source code (AST extraction)
   - Extract: Functions, HTTP endpoints, database schemas
   - Link: to ExternalConnections (endpoint matching)
   - Store: CodeArtifact nodes
   - Tools: Language-specific parsers (Go, Python, Java, TS)

3. **Test File Parsing** (which tests touch which services)
   - Parse: test_*.py, *Test.java, *.spec.ts
   - Extract: test type (UNIT/COMPONENT/INTEGRATION), mocks, real calls
   - Compute: which external services this test depends on
   - Store: TestCase nodes
   - Tools: AST parsers, mock detection heuristics

**Output**: Three CSV/JSON files ready to load into Neo4j

**Success**: All services ingested, >95% trace coverage, all tests classified

---

### Phase 2: Graph Foundation (Weeks 4–5)
**Goal**: Build queryable graph in Neo4j

**Neo4j schema**:
```
Node types:
  • Service (7 repos)
  • ExternalConnection (25+ inter-service calls + DBs + message topics)
  • CodeArtifact (340+ functions, endpoints, schemas)
  • TestCase (142+ unit/component/integration tests)
  • SchemaDefinition (Protobuf, SQL, JSON schemas)
  • ExternalResource (third-party APIs, DBs)
  • Change (Git commits)

Relationships:
  • Service -INITIATES→ ExternalConnection -TARGETS→ Service/ExternalResource
  • Service -DEFINES→ TestCase
  • TestCase -COVERS→ CodeArtifact
  • TestCase -DEPENDS_ON→ ExternalConnection
  • Service -CONTAINS→ CodeArtifact
  • CodeArtifact -EXPOSES→ ExternalConnection
  • ExternalConnection -USES_SCHEMA→ SchemaDefinition

Indexes:
  • CREATE INDEX ON Service(id)
  • CREATE INDEX ON CodeArtifact(id)
  • CREATE INDEX ON TestCase(id)
  • CREATE INDEX ON ExternalConnection(id)
```

**Data load**:
- Import Phase 1 CSVs
- Validate: no dangling refs, all relationships connect
- Test queries: can I find A→B→C paths?

**Success**: <100ms query latency, transitive queries work (depth 5), all data loaded

---

### Phase 3: Impact Analysis Engine (Weeks 6–7)
**Goal**: Given a change, find all affected services using 4 rules

**Rule 1: Direct Impact**
```cypher
MATCH (changed:Service)-[:INITIATES]->(conn)-[:TARGETS]->(affected:Service)
RETURN affected
// Who directly calls changed service?
```

**Rule 2: Transitive Impact (A→B→C)**
```cypher
MATCH (changed:Service)-[:INITIATES]->
      (conn1)-[:TARGETS]->(mid:Service),
      (mid)-[:INITIATES]->
      (conn2)-[:TARGETS]->(final:Service)
RETURN final
// Who is reachable via 2+ hops?
// Limit to depth 5 to avoid explosion
```

**Rule 3: Schema Impact**
```cypher
MATCH (changed:Service)-[:DEFINES]->(schema),
      (conn)-[:USES_SCHEMA]->(schema),
      (conn)-[:TARGETS]->(affected:Service)
RETURN affected
// Who uses the schema we changed?
```

**Rule 4: Reverse Dependency**
```cypher
MATCH (dependent:Service)-[:INITIATES]->(conn)-[:TARGETS]->(changed:Service)
RETURN dependent
// Who depends on the changed service?
```

**Confidence scoring**:
- Direct impact: 0.95 (traced)
- 1-hop transitive: 0.85
- 2-hop transitive: 0.75
- Schema impact: 0.80
- Reverse dep: 0.90

**Output**: ImpactAnalysisResult {
  affectedServices: [
    {service: "payment-service", confidence: 0.95, depth: 1, reason: "..."}
  ],
  impactChains: ["auth→payment→order"],
  transitiveServices: ["order-service"]
}

**Success**: All 4 rules implemented, tested on 5+ real changes, <500ms latency

---

### Phase 4: Test Selection (Weeks 8–9)
**Goal**: Convert impact analysis to test list

**Algorithm**:
```
1. For changed service: include ALL UNIT + COMPONENT tests (fast, comprehensive)
2. For affected services: include INTEGRATION tests that touch changed service
3. For breaking changes: include ALL tests (safety)
4. Deduplicate and sort by: priority, duration, flakiness
5. Apply test pyramid:
   - Tier 1 (UNIT): parallel, 5s
   - Tier 2 (COMPONENT): parallel, 10s
   - Tier 3 (INTEGRATION): parallel, 25s
   - Stop on failure (fast feedback)
```

**Output**: TestSelectionResult {
  tests: [
    {id: "test-jwt-verify", type: "UNIT", priority: "CRITICAL", duration: 150ms},
    {id: "test-payment-integration", type: "INTEGRATION", priority: "HIGH", duration: 2500ms}
  ],
  pyramid: {tier1: 5, tier2: 3, tier3: 8},
  estimatedDuration_ms: 40000
}

**Benefit**: 40s vs. 5+ min (80% reduction)

**Success**: <10% false negatives (missed tests), <20% false positives (unnecessary tests)

---

### Phase 5: CI Integration (Weeks 10–11)
**Goal**: Wire to GitHub Actions / Jenkins / GitLab CI

**API Server** (FastAPI):
```
POST /api/analyze
  Input: {repoId, commitHash, files}
  Process:
    1. Detect change
    2. Run impact analysis (Phase 3)
    3. Run test selector (Phase 4)
    4. Return test list
  Response: TestSelectionResult
  Latency: <500ms
```

**Git Webhook**:
```
On push → POST /api/analyze → get test list → CI runs selected tests
```

**CI Pipeline** (GitHub Actions example):
```yaml
jobs:
  impact-analysis:
    steps:
      - name: Analyze
        run: curl POST /api/analyze
      - name: Tier 1
        run: pytest [tier1 tests] --parallel
      - name: Tier 2
        if: tier1 passes
        run: pytest [tier2 tests] --parallel
      - name: Tier 3
        if: tier2 passes
        run: pytest [tier3 tests] --parallel
```

**Success**: API <500ms, CI time 80% reduction, team adoption >50%

---

### Phase 6: Observability & Feedback (Weeks 12–13)
**Goal**: Monitor, alert, improve

**Metrics**:
- Test flakiness (pass/fail ratio over time)
- Connection health (last seen, error rate)
- False positive rate (tests run but didn't fail)
- False negative rate (tests didn't run but failed)
- CI time trends (actual vs. estimated)
- Impact accuracy (predicted services vs. actual failures)

**Alerts**:
- ⚠️ Stale connection (critical service not seen in 48h)
- ⚠️ Circular dependency detected
- ⚠️ High false negative rate (>15%)
- ⚠️ Long impact chain (>5 hops)

**Dashboard**:
- Service dependency graph (interactive)
- Impact chains (A→B→C with weights)
- Test coverage heatmap
- CI time trends
- False pos/neg rates
- Circular dependencies

**Continuous sync**:
- Re-ingest Datadog traces: every 12 hours
- Re-parse test files: on every merge
- Re-extract code artifacts: on every merge

**Success**: Dashboard operational, false neg/pos rates stable, team confidence high

---

## Key Deliverables

| Phase | Week | Deliverable | File Size |
|-------|------|-------------|-----------|
| 1 | 1-3 | Datadog traces, tests, code artifacts (CSVs) | 50–100 MB |
| 2 | 4-5 | Neo4j database (7 services, 25 connections, 340 artifacts, 142 tests) | 1–5 GB |
| 3 | 6-7 | Impact analysis engine (4 rules) | Python: 500 LOC |
| 4 | 8-9 | Test selector + pyramid | Python: 300 LOC |
| 5 | 10-11 | API server + CI integration | Python: 200 LOC + YAML: 100 LOC |
| 6 | 12-13 | Dashboard + monitoring | Grafana/custom: TBD |

---

## Success Metrics (Target by Week 13)

| Metric | Target |
|--------|--------|
| CI time reduction | 80% (5 min → 1 min) |
| False negative rate | <10% (missed tests) |
| False positive rate | <20% (unnecessary tests) |
| API latency | <500ms |
| Transitive depth | up to 5 hops |
| Circular dep detection | 100% |
| Team adoption | >80% of commits use smart tests |

---

## Dependencies & Constraints

**Must-have**:
- Neo4j instance (self-hosted or managed)
- Datadog APM enabled on all services
- Git webhooks (GitHub, GitLab, or similar)
- CI system with API (GitHub Actions, Jenkins, GitLab CI)

**Nice-to-have**:
- OpenTelemetry integration (detailed traces)
- Slack notifications
- Automated schema diffing
- ML-based confidence refinement

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Incomplete Datadog traces | High | Sample 5 repos, validate >95% coverage |
| Wrong test categorization | High | Manual review of 20 random tests |
| Neo4j query perf issues | Medium | Index all node IDs, test <100ms latency |
| Circular dependencies | Medium | Detect in Phase 2, alert team |
| False negatives in prod | High | Beta with 1 team, monitor closely |
| Transitive depth explosion | Medium | Limit to depth 5, warn on long chains |

---

## Quick Start Checklist

- [ ] **Week 1**: Start Phase 1 (Datadog ingestion)
- [ ] **Week 4**: Phase 2 (Neo4j setup)
- [ ] **Week 6**: Phase 3 (impact rules)
- [ ] **Week 8**: Phase 4 (test selector)
- [ ] **Week 10**: Phase 5 (CI integration)
- [ ] **Week 12**: Phase 6 (observability)

---

## Files in This Package

1. **knowledge_graph_spec.md** — Original framework-agnostic knowledge graph (not used, reference only)
2. **impact_analysis_spec.md** — Original polyrepo impact analysis (v1, superseded)
3. **complete_system_design.md** — Full system design with Neo4j schema, 6 phases, pseudocode
4. **system_architecture.md** — Data flow diagram, Neo4j queries, example outputs, timeline
5. **This file** — Quick reference and executive summary

---

## Next Steps

1. **Clarify scope**:
   - How many services? (I assumed 7)
   - How many inter-service calls? (I assumed 25+)
   - How many tests? (I assumed 142)
   - Which Datadog plan do you have? (APM required)

2. **Start Phase 1**:
   - Set up Datadog API client
   - Design CSV format for Neo4j import
   - Begin tracing 1 service as a pilot

3. **Engage stakeholders**:
   - Data engineer (trace ingestion)
   - Database engineer (Neo4j)
   - Backend engineer (impact engine)
   - QA/DevOps (test selection + CI integration)

4. **Measure baseline**:
   - Current CI time per change
   - Current test count per service
   - Current false positive rate (if using naive test selection)

---

## Questions to Ask Yourself

1. Do you have Datadog APM enabled on all services? (Non-negotiable)
2. Are tests currently organized by service repo? (Yes, based on your description)
3. Do you have a test categorization standard (UNIT/COMPONENT/INTEGRATION)? (If not, Phase 1 will surface gaps)
4. Can you modify CI pipelines to accept a test list from an API? (Required for Phase 5)
5. Do you have Neo4j expertise in-house? (If not, plan 2–3 weeks of learning)

---

## Final Notes

- **This is not theoretical.** The design is based on real polyrepo patterns (microservices, distributed systems, enterprise software kits).
- **Neo4j is the secret weapon.** Once you have the graph, transitive queries are trivial. Without it, you're stuck with static analysis.
- **Datadog traces are your ground truth.** They tell you what's actually happening in production, not what you think should happen.
- **Start small.** Pilot with 1 service, 1 team, 1 change. Prove the concept before scaling to 7 services.
- **Iterate based on feedback.** False negatives (missed tests) are worse than false positives (unnecessary tests). Start conservative, loosen constraints as confidence grows.

---

**Good luck!** You're building something that will save your team hundreds of hours. 🚀
