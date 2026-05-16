# System Architecture Diagram (ASCII + Description)

## High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    POLYREPO IMPACT ANALYSIS SYSTEM                          │
│                     (Neo4j-Centric Architecture)                            │
└─────────────────────────────────────────────────────────────────────────────┘

╔════════════════════════════════════════════════════════════════════════════╗
║ DATA INGESTION LAYER (Phase 1)                                             ║
║ ─────────────────────────────────────────────────────────────────────────── ║
║                                                                             ║
║  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐         ║
║  │  Datadog APM     │  │  Git Repositories│  │  Test Files      │         ║
║  │  (Traces)        │  │  (Code Artifacts)│  │  (Test Cases)    │         ║
║  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘         ║
║           │                     │                     │                     ║
║           ├─────────────────────┼─────────────────────┤                     ║
║           │                     │                     │                     ║
║           ▼                     ▼                     ▼                     ║
║  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐         ║
║  │ Extract Traces:  │  │ Parse AST:       │  │ Parse Tests:     │         ║
║  │ • Service calls  │  │ • Functions      │  │ • Test type      │         ║
║  │ • DB queries     │  │ • Endpoints      │  │ • Dependencies   │         ║
║  │ • Message topics │  │ • Schemas        │  │ • Coverage       │         ║
║  │ • Frequency      │  │ • Visibility     │  │ • Duration       │         ║
║  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘         ║
║           │                     │                     │                     ║
║           └─────────────────────┴─────────────────────┘                     ║
║                                 │                                           ║
║                                 ▼                                           ║
║                    ┌─────────────────────────┐                             ║
║                    │ Create Intermediate     │                             ║
║                    │ JSONs/CSVs for Neo4j    │                             ║
║                    └─────────────┬───────────┘                             ║
║                                  │                                          ║
╚══════════════════════════════════╪═══════════════════════════════════════════╝
                                   │
╔══════════════════════════════════▼═══════════════════════════════════════════╗
║ GRAPH DATABASE LAYER (Phase 2)                                              ║
║ ─────────────────────────────────────────────────────────────────────────── ║
║                                                                             ║
║                    ┌─────────────────────────┐                             ║
║                    │   NEO4J DATABASE        │                             ║
║                    │                         │                             ║
║                    │  Nodes:                 │                             ║
║                    │  • Service (7)          │                             ║
║                    │  • ExternalConnection   │                             ║
║                    │    (25)                 │                             ║
║                    │  • CodeArtifact (340)   │                             ║
║                    │  • TestCase (142)       │                             ║
║                    │  • SchemaDefinition     │                             ║
║                    │  • ExternalResource     │                             ║
║                    │  • Change               │                             ║
║                    │                         │                             ║
║                    │  Relationships:         │                             ║
║                    │  • Service-INITIATES->  │                             ║
║                    │    ExternalConnection   │                             ║
║                    │  • ExternalConnection-  │                             ║
║                    │    TARGETS->Service     │                             ║
║                    │  • Service-DEFINES->    │                             ║
║                    │    TestCase             │                             ║
║                    │  • TestCase-COVERS->    │                             ║
║                    │    CodeArtifact         │                             ║
║                    │  • CodeArtifact-        │                             ║
║                    │    EXPOSES->            │                             ║
║                    │    ExternalConnection   │                             ║
║                    │  • Service-CONTAINS->   │                             ║
║                    │    CodeArtifact         │                             ║
║                    │                         │                             ║
║                    └────────────┬────────────┘                             ║
║                                 │                                          ║
║                    Indexes:      │                                          ║
║                    • service.id  │                                          ║
║                    • artifact.id │                                          ║
║                    • test.id     │                                          ║
║                    • conn.id     │                                          ║
║                                 │                                          ║
║                  Query Engine:   │                                          ║
║                    • Transitive  │                                          ║
║                    • Circular    │                                          ║
║                    • Impact      │                                          ║
║                                  │                                          ║
╚══════════════════════════════════╪═══════════════════════════════════════════╝
                                   │
╔══════════════════════════════════▼═══════════════════════════════════════════╗
║ IMPACT ANALYSIS ENGINE (Phase 3)                                            ║
║ ─────────────────────────────────────────────────────────────────────────── ║
║                                                                             ║
║  Input: Change object {repoId, files, type}                                ║
║                                                                             ║
║  ┌─ Rule 1: Direct Impact ─────────────────────────────────────┐           ║
║  │ Query: (changed:Service)-[:INITIATES]-(conn)-[:TARGETS]-    │           ║
║  │        (affected:Service)                                    │           ║
║  │ Returns: Direct callers of changed service                  │           ║
║  └──────────────────────────────────────────────────────────────┘           ║
║                                                                             ║
║  ┌─ Rule 2: Transitive Impact ────────────────────────────────┐            ║
║  │ Query: (changed:Service)-[:INITIATES]->                     │            ║
║  │        (conn1)-[:TARGETS]->(mid:Service)-                   │            ║
║  │        [:INITIATES]->(conn2)-[:TARGETS]->(final:Service)    │            ║
║  │ Returns: All reachable services (A→B→C→D...)               │            ║
║  └──────────────────────────────────────────────────────────────┘           ║
║                                                                             ║
║  ┌─ Rule 3: Schema Impact ────────────────────────────────────┐            ║
║  │ Query: (changed:Service)-[:DEFINES]->(schema)...           │            ║
║  │ Returns: Services using changed schema                      │            ║
║  └──────────────────────────────────────────────────────────────┘           ║
║                                                                             ║
║  ┌─ Rule 4: Reverse Dependency ──────────────────────────────┐             ║
║  │ Query: (dep:Service)-[:INITIATES]->(conn)-[:TARGETS]->     │             ║
║  │        (changed:Service)                                    │             ║
║  │ Returns: Services that depend on changed service           │             ║
║  └──────────────────────────────────────────────────────────────┘           ║
║                                                                             ║
║  Output: ImpactAnalysisResult {                                            ║
║    affectedServices: [                                                     ║
║      {service: "payment", confidence: 0.95, depth: 1, chain: [...]}       ║
║    ],                                                                       ║
║    impactChains: ["auth→payment→order", ...],                             ║
║    transitiveServices: ["order-service", "notification-service"]          ║
║  }                                                                          ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
                                   │
╔══════════════════════════════════▼═══════════════════════════════════════════╗
║ TEST SELECTION LAYER (Phase 4)                                              ║
║ ─────────────────────────────────────────────────────────────────────────── ║
║                                                                             ║
║  Input: ImpactAnalysisResult                                               ║
║                                                                             ║
║  Algorithm:                                                                ║
║  1. For changed service: include UNIT + COMPONENT tests (fast)            ║
║  2. For affected services: include INTEGRATION tests (thorough)            ║
║  3. For breaking changes: include ALL tests (safety)                       ║
║  4. Apply test pyramid: tier by type, parallel within tier                ║
║  5. Sort by: priority, duration, flakiness                                ║
║                                                                             ║
║  Output: TestSelectionResult {                                            ║
║    tests: [                                                               ║
║      {id: "test-jwt-verify", type: "UNIT", priority: "CRITICAL", ...}    ║
║    ],                                                                      ║
║    pyramid: {                                                             ║
║      tier1_unit: 5 tests (5s),                                            ║
║      tier2_component: 3 tests (10s),                                      ║
║      tier3_integration: 8 tests (25s)                                     ║
║    },                                                                      ║
║    estimatedDuration_ms: 40000                                            ║
║  }                                                                          ║
║                                                                             ║
║  Benefit: 40s vs. 5+ min if running all tests                             ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
                                   │
╔══════════════════════════════════▼═══════════════════════════════════════════╗
║ API SERVER (Phase 5)                                                        ║
║ ─────────────────────────────────────────────────────────────────────────── ║
║                                                                             ║
║  FastAPI Server                                                            ║
║                                                                             ║
║  POST /api/analyze                                                         ║
║    Input: {repoId, commitHash, files}                                      ║
║    Process:                                                                ║
║      1. Fetch Change from Neo4j                                            ║
║      2. Run ImpactAnalysisEngine (Phase 3)                                 ║
║      3. Run TestSelector (Phase 4)                                         ║
║      4. Return result                                                      ║
║    Response: TestSelectionResult                                           ║
║    Latency: <500ms                                                         ║
║                                                                             ║
║  GET /api/stats                                                            ║
║    Returns: System health metrics                                          ║
║                                                                             ║
║  GET /api/graph/{serviceId}                                                ║
║    Returns: Service dependency graph (for visualization)                   ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
                                   │
╔══════════════════════════════════▼═══════════════════════════════════════════╗
║ CI INTEGRATION (Phase 5)                                                    ║
║ ─────────────────────────────────────────────────────────────────────────── ║
║                                                                             ║
║  Git Webhook Listener                                                      ║
║    Trigger: on push / PR                                                   ║
║    Action: POST /api/analyze                                               ║
║                                                                             ║
║  GitHub Actions / Jenkins / GitLab CI                                      ║
║                                                                             ║
║  ┌─ Tier 1: Unit Tests ──────────────────────────────────┐                ║
║  │ Run in parallel                                        │                ║
║  │ Stop on first failure (fast feedback)                 │                ║
║  │ Time: ~5s                                              │                ║
║  └────────────────────────────────────────────────────────┘                ║
║           ↓ (only if tier 1 passes)                                        ║
║  ┌─ Tier 2: Component Tests ────────────────────────────┐                 ║
║  │ Run in parallel                                        │                 ║
║  │ Time: ~10s                                             │                 ║
║  └────────────────────────────────────────────────────────┘                ║
║           ↓ (only if tier 2 passes)                                        ║
║  ┌─ Tier 3: Integration Tests ──────────────────────────┐                 ║
║  │ Run in parallel                                        │                 ║
║  │ Time: ~25s                                             │                 ║
║  └────────────────────────────────────────────────────────┘                ║
║           ↓                                                                 ║
║  Result: PASS / FAIL                                                       ║
║                                                                             ║
║  Store Result in Neo4j:                                                    ║
║  • Test execution time (for flakiness tracking)                            ║
║  • Pass/fail status                                                        ║
║  • Coverage data                                                           ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
                                   │
╔══════════════════════════════════▼═══════════════════════════════════════════╗
║ OBSERVABILITY & FEEDBACK LOOP (Phase 6)                                     ║
║ ─────────────────────────────────────────────────────────────────────────── ║
║                                                                             ║
║  Metrics Collected:                                                        ║
║  • Test flakiness (pass/fail ratio over time)                              ║
║  • Connection health (last seen, error rate)                               ║
║  • False positive rate (tests run but didn't fail)                         ║
║  • False negative rate (tests didn't run but failed)                       ║
║  • CI time trends (actual vs. estimated)                                   ║
║  • Impact chain accuracy (predicted vs. actual)                            ║
║                                                                             ║
║  Alerts:                                                                   ║
║  ⚠️  Stale connection (critical conn not seen in 48h)                      ║
║  ⚠️  Circular dependency detected                                          ║
║  ⚠️  High false negative rate (>15%)                                       ║
║  ⚠️  Long impact chain (>5 hops)                                           ║
║                                                                             ║
║  Dashboard (Grafana / Custom):                                             ║
║  • Service dependency graph (interactive Neo4j visualization)              ║
║  • Impact chains (A→B→C with weights)                                      ║
║  • Test coverage heatmap                                                   ║
║  • CI time trends                                                          ║
║  • False pos/neg rates over time                                           ║
║  • Circular dependencies                                                   ║
║                                                                             ║
║  Continuous Sync:                                                          ║
║  • Re-ingest Datadog traces every 12 hours                                 ║
║  • Re-parse test files on every merge                                      ║
║  • Re-extract code artifacts on every merge                                ║
║  • Update connection frequency/criticality                                 ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
```

## Key Data Structures

### Service
```
{
  id: "auth-service",
  name: "Auth Service",
  repoUrl: "https://github.com/company/auth-service",
  language: "go",
  framework: "gin",
  owner: "platform-team"
}
```

### ExternalConnection
```
{
  id: "conn-auth-payment-1",
  type: "HTTP_CALL",
  protocol: "http",
  endpoint: "POST /api/charges",
  direction: "OUTBOUND",
  frequency: 500,  // calls per minute
  criticality: "CRITICAL",
  discoveredAt: "2024-05-16T10:00:00Z",
  lastObservedAt: "2024-05-16T15:30:00Z"
}
```

### CodeArtifact
```
{
  id: "artifact-jwt-generate",
  type: "FUNCTION",
  name: "GenerateJWT",
  repoId: "auth-service",
  file: "internal/auth/jwt.go",
  lineRange: {start: 42, end: 58},
  isPublic: true,
  version: "1.0.0"
}
```

### TestCase
```
{
  id: "test-jwt-verify",
  name: "TestJWTVerification",
  repoId: "auth-service",
  type: "UNIT",
  file: "internal/auth/jwt_test.go",
  duration_ms: 150,
  flakiness_score: 0.02,  // very stable
  priority: "CRITICAL"
}
```

## Neo4j Query Examples

### Find all services affected by change in auth-service
```cypher
MATCH (auth:Service {id: "auth-service"})-[:INITIATES]->
      (conn:ExternalConnection)-[:TARGETS]->(affected:Service)
RETURN DISTINCT affected.name, conn.criticality
```

### Find transitive impact (A→B→C)
```cypher
MATCH (auth:Service {id: "auth-service"})-[:INITIATES]->
      (conn1:ExternalConnection)-[:TARGETS]->(payment:Service),
      (payment)-[:INITIATES]->
      (conn2:ExternalConnection)-[:TARGETS]->(order:Service)
RETURN auth.name, payment.name, order.name
```

### Find tests to run if auth-service changes
```cypher
MATCH (auth:Service {id: "auth-service"})-[:DEFINES]->(test:TestCase)
WHERE test.type IN ['UNIT', 'COMPONENT']
RETURN test.name, test.type, test.duration_ms
ORDER BY test.priority DESC
```

### Detect circular dependencies
```cypher
MATCH (s1:Service)-[:INITIATES]->
      (c1:ExternalConnection)-[:TARGETS]->(s2:Service),
      (s2)-[:INITIATES]->
      (c2:ExternalConnection)-[:TARGETS]->(s3:Service),
      (s3)-[:INITIATES]->
      (c3:ExternalConnection)-[:TARGETS]->(s1)
RETURN s1.name, s2.name, s3.name
```

## Example Impact Analysis Output

```json
{
  "changeId": "commit-abc123",
  "timestamp": "2024-05-16T15:45:00Z",
  "affectedServices": [
    {
      "serviceId": "payment-service",
      "confidence": 0.95,
      "depth": 1,
      "reason": "Calls auth-service's verify-token endpoint"
    },
    {
      "serviceId": "order-service",
      "confidence": 0.85,
      "depth": 2,
      "reason": "Transitively: order→payment→auth"
    }
  ],
  "impactChains": [
    ["auth-service", "payment-service"],
    ["auth-service", "payment-service", "order-service"]
  ],
  "recommendedTests": [
    {
      "testId": "test-jwt-verify",
      "repoId": "auth-service",
      "type": "UNIT",
      "priority": "CRITICAL",
      "duration_ms": 150
    },
    {
      "testId": "test-payment-jwt-validation",
      "repoId": "payment-service",
      "type": "INTEGRATION",
      "priority": "HIGH",
      "duration_ms": 2500
    }
  ],
  "estimatedCIPipelineTime_ms": 40000,
  "breakingChanges": []
}
```

## Timeline

| Week | Phase | Deliverable |
|------|-------|-------------|
| 1-3 | 1 | Data ingestion (Datadog, tests, code artifacts) |
| 4-5 | 2 | Neo4j database + graph queries |
| 6-7 | 3 | Impact analysis engine (4 rules) |
| 8-9 | 4 | Test selection algorithm |
| 10-11 | 5 | API server + CI integration |
| 12-13 | 6 | Dashboard + monitoring |

