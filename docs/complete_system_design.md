# Multi-Repo Impact Analysis: Complete System Design
## Neo4j-Centric Architecture with Transitive Dependency Tracking

**Version**: 1.0  
**Focus**: Graph as foundation; phases for incremental delivery.

---

## Part 1: Graph Database Schema (Neo4j)

### Node Types & Properties

#### 1.1 Service Node
```cypher
CREATE (s:Service {
  id: String (PK),
  name: String,
  repoUrl: String,
  language: String,
  framework: String,
  owner: String,
  createdAt: DateTime,
  lastUpdatedAt: DateTime,
  isActive: Boolean
})
```

#### 1.2 ExternalConnection Node (Edge type, not Cypher edge)
Why a node, not an edge? **Connections have rich metadata**: frequency, criticality, dataFlow, status.

```cypher
CREATE (conn:ExternalConnection {
  id: String (PK),
  type: String (HTTP_CALL, GRPC_CALL, MESSAGE_QUEUE, DATABASE, CACHE, EXTERNAL_API),
  protocol: String,
  endpoint: String (e.g., 'POST /api/charges', 'table:users', 'topic:user-events'),
  direction: String (INBOUND, OUTBOUND),
  frequency: Float (calls per minute),
  criticality: String (CRITICAL, HIGH, MEDIUM, LOW),
  contractStatus: String (STABLE, EVOLVING, DEPRECATED, BREAKING),
  dataFlow: {
    requestFormat: String,
    responseFormat: String,
    schemaVersion: String
  },
  discoveredAt: DateTime,
  lastObservedAt: DateTime
})
```

Relationships:
```cypher
(service1:Service)-[:INITIATES]->(conn:ExternalConnection)-[:TARGETS]->(service2:Service | external:ExternalResource)
```

Example:
```cypher
(auth:Service)-[:INITIATES {weight: 500}]->(conn:ExternalConnection {type: 'HTTP_CALL'})-[:TARGETS]->(payment:Service)
```

#### 1.3 CodeArtifact Node
```cypher
CREATE (artifact:CodeArtifact {
  id: String (PK),
  type: String (FUNCTION, HTTP_ENDPOINT, GRPC_METHOD, MESSAGE_HANDLER, SCHEMA_DEFINITION, DATABASE_MIGRATION),
  name: String,
  repoId: String (FK to Service),
  file: String (relative path),
  lineRange: { start: Int, end: Int },
  isPublic: Boolean (external consumers can depend on it),
  version: String (semantic versioning for public APIs),
  createdAt: DateTime
})
```

Relationships:
```cypher
(service:Service)-[:CONTAINS]->(artifact:CodeArtifact)
(artifact:CodeArtifact)-[:EXPOSES]->(conn:ExternalConnection)
```

#### 1.4 TestCase Node
```cypher
CREATE (test:TestCase {
  id: String (PK),
  name: String,
  repoId: String (FK to Service),
  type: String (UNIT, COMPONENT, INTEGRATION),
  file: String,
  lineRange: { start: Int, end: Int },
  duration_ms: Int,
  flakiness_score: Float (0.0–1.0),
  priority: String (CRITICAL, HIGH, MEDIUM, LOW),
  createdAt: DateTime
})
```

Relationships:
```cypher
(service:Service)-[:DEFINES]->(test:TestCase)
(test:TestCase)-[:COVERS]->(artifact:CodeArtifact)
(test:TestCase)-[:DEPENDS_ON]->(conn:ExternalConnection) {
  type: String (MOCK, STUB, REAL_CALL, SCHEMA_VALIDATION),
  isRequired: Boolean
}
```

#### 1.5 Change Node
```cypher
CREATE (change:Change {
  id: String (PK, commit hash),
  repoId: String,
  type: String (CODE, SCHEMA, CONFIG, API_CONTRACT),
  timestamp: DateTime,
  description: String,
  isBreaking: Boolean,
  createdAt: DateTime
})
```

Relationships:
```cypher
(change:Change)-[:MODIFIES]->(artifact:CodeArtifact)
(change:Change)-[:AFFECTS]->(conn:ExternalConnection)
```

#### 1.6 SchemaDefinition Node
```cypher
CREATE (schema:SchemaDefinition {
  id: String (PK),
  name: String (e.g., 'User', 'Order', 'PaymentEvent'),
  type: String (PROTOBUF, JSON_SCHEMA, SQL_TABLE, KAFKA_TOPIC),
  repoId: String,
  version: String,
  definition: String (raw schema text),
  createdAt: DateTime
})
```

Relationships:
```cypher
(service:Service)-[:DEFINES]->(schema:SchemaDefinition)
(conn:ExternalConnection)-[:USES_SCHEMA]->(schema:SchemaDefinition)
```

#### 1.7 ExternalResource Node
For resources outside your polyrepo: databases, message queues, third-party APIs.

```cypher
CREATE (ext:ExternalResource {
  id: String (PK),
  name: String (e.g., 'stripe-api', 'users-postgres', 'analytics-kafka'),
  type: String (EXTERNAL_API, DATABASE, MESSAGE_QUEUE, CACHE),
  endpoint: String,
  criticality: String (CRITICAL, HIGH, MEDIUM, LOW),
  owner: String or null,
  documentation: String or null
})
```

---

## Part 2: Transitive Dependency Tracking

### Queries

#### 2.1 Direct Dependencies (1-hop)
```cypher
MATCH (serviceA:Service {id: 'auth-service'})-[:INITIATES]->
      (conn:ExternalConnection)-[:TARGETS]->(serviceB:Service)
RETURN serviceB.name, conn.type, conn.criticality
// Result: payment-service calls, order-service calls, etc.
```

#### 2.2 Transitive Dependencies (A→B→C)
```cypher
MATCH (serviceA:Service {id: 'auth-service'})-[:INITIATES]->
      (conn1:ExternalConnection)-[:TARGETS]->(serviceB:Service),
      (serviceB)-[:INITIATES]->
      (conn2:ExternalConnection)-[:TARGETS]->(serviceC:Service)
RETURN serviceB.name, serviceC.name, conn1.criticality, conn2.criticality
// Result: If auth changes, payment might change, and payment calls order
//         → order is transitively affected
```

#### 2.3 All Affected Services (Recursive)
```cypher
MATCH (changed:Service {id: 'auth-service'})-[:INITIATES]->
      (conn:ExternalConnection)-[:TARGETS]->(affected:Service)
WITH changed, affected, [affected] as impacted
CALL {
  WITH affected
  MATCH (affected)-[:INITIATES]->
        (conn2:ExternalConnection)-[:TARGETS]->(next:Service)
  RETURN next
} (Recursive until depth limit)
RETURN DISTINCT impacted
// Returns all services affected transitively
```

#### 2.4 Impact Chain (Show path)
```cypher
MATCH path = (changed:Service {id: 'auth-service'})-[:INITIATES*]->
            (conn:ExternalConnection)-[:TARGETS]->(affected:Service)
RETURN [rel in relationships(path) | type(rel)] as chain,
       [node in nodes(path) | node.name] as services,
       [conn in [rel in relationships(path) WHERE type(rel) = 'TARGETS'] | conn.criticality] as criticality
// Shows: auth → payment [CRITICAL] → order [HIGH] → notification [MEDIUM]
```

#### 2.5 Circular Dependencies Detection
```cypher
MATCH (s:Service)-[:INITIATES]->(c1:ExternalConnection)-[:TARGETS]->(s2:Service)-[:INITIATES]->
      (c2:ExternalConnection)-[:TARGETS]->(s3:Service)-[:INITIATES]->
      (c3:ExternalConnection)-[:TARGETS]->(s:Service)
RETURN s.name, s2.name, s3.name, c1.criticality, c2.criticality, c3.criticality
// Warning: circular dependencies exist
```

#### 2.6 Impact Analysis Query (Core)
```cypher
// Given a change in auth-service, find all affected services and their tests
MATCH (change:Change {id: 'commit-abc123'})-[:MODIFIES]->(artifact:CodeArtifact),
      (artifact)-[:EXPOSES]->(conn:ExternalConnection),
      (conn)-[:TARGETS]->(affected:Service)
      
// Direct impact
WITH affected, conn, change
MATCH (affected)-[:DEFINES]->(test:TestCase)

// Transitive impact (A→B→C)
OPTIONAL MATCH (affected)-[:INITIATES]->(conn2:ExternalConnection)-[:TARGETS]->(transitive:Service),
              (transitive)-[:DEFINES]->(test2:TestCase)

RETURN affected.name as service,
       collect(DISTINCT test.name) as directTests,
       collect(DISTINCT test2.name) as transitiveTests,
       collect(DISTINCT transitive.name) as transitiveServices,
       conn.criticality,
       change.isBreaking
ORDER BY conn.criticality DESC
```

---

## Part 3: Complete System Architecture

### System Diagram (Text-based)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         Multi-Repo Impact Analysis System                       │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─ PHASE 1: Foundation (Weeks 1-3) ──────────────────────────────────────────────┐
│                                                                                  │
│  [Datadog APM Ingestion]                                                       │
│    ↓                                                                             │
│  [Extract Traces] → ExternalConnection registry                                │
│    │                                                                             │
│    ├─ Query last 30 days of production spans                                   │
│    ├─ Group by (source_service, target_service, endpoint)                      │
│    ├─ Compute frequency, latency, error rates                                  │
│    └─ Store to Neo4j (Service)-[:INITIATES]->(ExternalConnection)-[:TARGETS]   │
│                                                                                  │
│  [Test Ingestion]                                                               │
│    ↓                                                                             │
│  [Extract test files] → TestCase registry                                      │
│    │                                                                             │
│    ├─ Parse *.test.py, *Test.java, *.spec.ts                                   │
│    ├─ Extract: test name, type (UNIT/COMPONENT/INTEGRATION)                    │
│    ├─ Extract: which external services are mocked vs. real                      │
│    └─ Store to Neo4j (Service)-[:DEFINES]->(TestCase)                          │
│                                                                                  │
│  [Code Artifact Ingestion]                                                      │
│    ↓                                                                             │
│  [Extract from AST] → CodeArtifact registry                                    │
│    │                                                                             │
│    ├─ Static analysis: functions, endpoints, schemas                           │
│    ├─ Link artifacts to ExternalConnections                                    │
│    └─ Store to Neo4j (Service)-[:CONTAINS]->(CodeArtifact)                     │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ PHASE 2: Graph Foundation (Weeks 4-5) ────────────────────────────────────────┐
│                                                                                  │
│  Neo4j Database Setup                                                           │
│    ↓                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                     NEO4J GRAPH DATABASE                               │   │
│  │  ┌─────────────┐                                                       │   │
│  │  │  Service    │                                                       │   │
│  │  │  (7 repos)  │                                                       │   │
│  │  └──────┬──────┘                                                       │   │
│  │         │                                                              │   │
│  │         ├─[:INITIATES]→ ExternalConnection ──[:TARGETS]→ Service      │   │
│  │         │                      (25 conns)                              │   │
│  │         ├─[:DEFINES]→ TestCase (142 tests)                            │   │
│  │         │              │                                               │   │
│  │         │              └─[:COVERS]→ CodeArtifact                      │   │
│  │         │              └─[:DEPENDS_ON]→ ExternalConnection            │   │
│  │         │                                                              │   │
│  │         └─[:CONTAINS]→ CodeArtifact (340 artifacts)                   │   │
│  │                        │                                               │   │
│  │                        └─[:EXPOSES]→ ExternalConnection               │   │
│  │                                                                         │   │
│  │         ExternalResource (3rd-party APIs, DBs, queues)               │   │
│  │         SchemaDefinition (Protobuf, SQL, JSON schemas)               │   │
│  │         Change (Git commits, PRs)                                     │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  Graph Queries Enabled:                                                        │
│    ✓ Direct dependencies (1-hop)                                               │
│    ✓ Transitive dependencies (A→B→C→...→N)                                     │
│    ✓ Circular dependency detection                                             │
│    ✓ Service impact chains (with criticality weighting)                        │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ PHASE 3: Impact Analysis Engine (Weeks 6-7) ──────────────────────────────────┐
│                                                                                  │
│  Implement 4 Rules Engine:                                                      │
│    │                                                                             │
│    ├─ Rule 1: Direct Service Impact                                            │
│    │  Query: (changed:Service)-[:INITIATES]-(conn)-[:TARGETS]-(affected)       │
│    │  Queries Neo4j → returns immediate callers                                │
│    │                                                                             │
│    ├─ Rule 2: Transitive Service Impact (A→B→C)                                │
│    │  Query: Recursive match with depth limit (3-5 hops)                       │
│    │  Queries Neo4j → returns all reachable services                           │
│    │                                                                             │
│    ├─ Rule 3: Schema Impact                                                    │
│    │  Query: (change:Change)-[:AFFECTS]-(schema:SchemaDefinition)-              │
│    │         (ExternalConnection)-[:TARGETS]-(affected:Service)                 │
│    │  Queries Neo4j → returns all services using changed schema                │
│    │                                                                             │
│    └─ Rule 4: Reverse Dependency Impact                                        │
│       Query: (changed:Service)-[:INITIATES]-(conn)-[:TARGETS]-(dependent)      │
│       Queries Neo4j → returns all services that depend on this service         │
│                                                                                  │
│  Output: ImpactAnalysisResult {                                                │
│    affectedServices: [                                                          │
│      { service: "payment-service", confidence: 0.95, depth: 1, reason: "..." } │
│    ],                                                                            │
│    impactChains: [                                                              │
│      ["auth-service" → "payment-service" → "order-service" → ...]              │
│    ],                                                                            │
│    transitiveServices: ["order-service", "notification-service"]               │
│  }                                                                               │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ PHASE 4: Test Selection & Mapping (Weeks 8-9) ────────────────────────────────┐
│                                                                                  │
│  Link ImpactAnalysisResult to TestCases:                                        │
│    │                                                                             │
│    ├─ For each affected service, query:                                        │
│    │  (affected:Service)-[:DEFINES]->(test:TestCase)                           │
│    │                                                                             │
│    ├─ Filter by test.type:                                                     │
│    │  - If service == changed service: include UNIT + COMPONENT                │
│    │  - If service is transitive: include INTEGRATION                          │
│    │                                                                             │
│    ├─ Filter by test dependencies:                                             │
│    │  - Include tests that (test)-[:DEPENDS_ON]-(conn)-[:TARGETS]-(changed)   │
│    │                                                                             │
│    └─ Sort by: priority, criticality, duration                                 │
│                                                                                  │
│  Output: TestSelectionResult {                                                 │
│    criticalTests: [test-payment-jwt],                                          │
│    highTests: [test-order-integration],                                        │
│    mediumTests: [test-notification-integration],                               │
│    estimatedDuration_ms: 40000,                                                │
│    testPyramid: { unit: 5, component: 3, integration: 8 }                      │
│  }                                                                               │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ PHASE 5: CI Integration (Weeks 10-11) ────────────────────────────────────────┐
│                                                                                  │
│  [Git Webhook]                                                                  │
│    ↓                                                                             │
│  [Detect Change] → POST /api/analyze                                           │
│    ↓                                                                             │
│  [Impact Analysis Engine]                                                       │
│    │ (uses Neo4j queries from Phase 3)                                         │
│    ├─ Query affected services (direct + transitive)                            │
│    └─ Query test selection (Phase 4)                                           │
│    ↓                                                                             │
│  [Test Selection Result]                                                        │
│    ↓                                                                             │
│  [GitHub Actions / Jenkins / GitLab CI]                                        │
│    ├─ Tier 1: Run UNIT tests (parallel)                                       │
│    ├─ Tier 2: Run COMPONENT tests (parallel)                                  │
│    └─ Tier 3: Run INTEGRATION tests (parallel)                                │
│                                                                                  │
│  Result:                                                                        │
│    ✓ Auth change → 40s CI (vs. 5+ min if all tests)                            │
│    ✓ Payment schema change → 90s CI (includes dependent services)              │
│    ✓ Transitive propagation → order-service tests auto-selected                │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ PHASE 6: Observability & Feedback Loop (Weeks 12-13) ─────────────────────────┐
│                                                                                  │
│  Monitor & Learn:                                                               │
│    │                                                                             │
│    ├─ Track test execution results → update TestCase.flakiness_score           │
│    ├─ Track ExternalConnection health → update .lastObservedAt, error_rate     │
│    ├─ Detect false positives → adjust impact rules confidence                  │
│    └─ Alert on stale connections → "payment-service not called in 48h"        │
│                                                                                  │
│  Dashboard:                                                                     │
│    ├─ Service dependency graph (visual)                                        │
│    ├─ Impact chains (A→B→C)                                                    │
│    ├─ Test coverage by connection                                              │
│    ├─ CI time trends (improving with selective tests)                          │
│    └─ Circular dependency alerts                                               │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 4: Detailed Phase Breakdown

### PHASE 1: Data Ingestion (Weeks 1–3)

#### 1.1 Datadog APM Ingestion
**Goal**: Extract all service-to-service calls, message queue topics, database queries.

**Inputs**:
- Datadog API key
- List of service names (auth-service, payment-service, order-service, etc.)
- Trace retention period (7–30 days of production data)

**Process**:
```python
# Pseudocode
for service in services:
  traces = datadog_api.get_traces(
    service=service,
    tag='environment:production',
    span_type=['http', 'db', 'queue']
  )
  
  for trace in traces:
    for span in trace.spans:
      if span.target_service:
        conn = ExternalConnection(
          type=span.type,  # HTTP_CALL, DB_QUERY, MESSAGE_QUEUE
          source=service,
          target=span.target_service,
          endpoint=span.resource,  # e.g., 'POST /api/charges'
          frequency=span.call_count_per_minute,
          criticality=compute_criticality(span.error_rate),
          dataFlow=span.payload_schema
        )
        neo4j.create_connection(conn)
```

**Output**: Neo4j ExternalConnection nodes + relationships.

**Success Criteria**:
- ✓ 100% of production services ingested
- ✓ All HTTP endpoints captured (>95% match with code)
- ✓ All database tables captured (>95% match with migrations)
- ✓ All message topics captured (>95% match with producers/consumers)

---

#### 1.2 Test Case Ingestion
**Goal**: Extract all test files, identify what they test, what they depend on.

**Inputs**:
- Each service repo
- Test file patterns (test_*.py, *Test.java, *.spec.ts, etc.)

**Process**:
```python
# Pseudocode
for service in services:
  for test_file in service.find_test_files():
    ast = parse(test_file)
    
    for test_func in ast.test_functions:
      # Detect test type
      test_type = classify_test(test_func)  # UNIT, COMPONENT, INTEGRATION
      
      # Extract mocks/stubs (mocked dependencies)
      mocks = extract_mocks(test_func)  # {payment-service, user-db}
      
      # Extract real calls (real dependencies)
      real_calls = extract_real_calls(test_func)  # {user-db}
      
      # Extract assertions to find covered artifacts
      covered_artifacts = extract_artifact_coverage(test_func)
      
      # Determine affected repos
      affected_repos = []
      if test_type == 'INTEGRATION':
        for mock in mocks + real_calls:
          # Which service does this mock/call belong to?
          target_service = find_target_service(mock)
          affected_repos.append(target_service)
      
      test = TestCase(
        name=test_func.name,
        repoId=service.id,
        type=test_type,
        duration_ms=estimate_duration(test_func),
        coveredArtifacts=covered_artifacts,
        affectedRepos=affected_repos,
        dependencies={
          'mocked': mocks,
          'real': real_calls
        }
      )
      neo4j.create_test_case(test)
      
      # Link test to covered artifacts
      for artifact_id in covered_artifacts:
        neo4j.create_relationship(test, 'COVERS', artifact_id)
```

**Output**: Neo4j TestCase nodes + COVERS, DEPENDS_ON relationships.

**Success Criteria**:
- ✓ All test files parsed
- ✓ Test type correctly classified (validate via manual sampling)
- ✓ Mocks vs. real calls correctly identified
- ✓ Affected repos correctly mapped

---

#### 1.3 Code Artifact Extraction
**Goal**: Extract functions, endpoints, schemas; link to external connections.

**Inputs**:
- Each service repo
- AST parsers (language-specific)

**Process**:
```python
# Pseudocode (from knowledge graph spec Phase 1–2)
for service in services:
  artifacts = []
  
  # Extract functions, endpoints, schemas via AST
  ast = parse_service_codebase(service)
  
  for item in ast.all_declarations:
    artifact = CodeArtifact(
      name=item.name,
      type=classify_type(item),  # FUNCTION, HTTP_ENDPOINT, SCHEMA, etc.
      repoId=service.id,
      file=item.file,
      isPublic=item.visibility == 'PUBLIC'
    )
    neo4j.create_artifact(artifact)
    artifacts.append(artifact)
  
  # Link artifacts to external connections
  for artifact in artifacts:
    for conn in external_connections:
      if artifact_matches_connection(artifact, conn):
        # e.g., HTTP endpoint POST /api/charges matches conn to payment-service
        neo4j.create_relationship(artifact, 'EXPOSES', conn)
```

**Output**: Neo4j CodeArtifact nodes + EXPOSES relationships.

**Success Criteria**:
- ✓ All public APIs extracted
- ✓ All database tables/schemas extracted
- ✓ All message handlers extracted
- ✓ Artifacts correctly linked to connections (>90% precision)

---

### PHASE 2: Graph Foundation (Weeks 4–5)

#### 2.1 Neo4j Setup
**Goal**: Initialize database, define indexes, constraints.

**Schema**:
```cypher
// Indexes for fast lookups
CREATE INDEX service_id ON :Service(id);
CREATE INDEX artifact_id ON :CodeArtifact(id);
CREATE INDEX test_id ON :TestCase(id);
CREATE INDEX conn_id ON :ExternalConnection(id);
CREATE INDEX change_id ON :Change(id);

// Constraints
CREATE CONSTRAINT service_pk ON (s:Service) ASSERT s.id IS UNIQUE;
CREATE CONSTRAINT artifact_pk ON (a:CodeArtifact) ASSERT a.id IS UNIQUE;
CREATE CONSTRAINT test_pk ON (t:TestCase) ASSERT t.id IS UNIQUE;
```

#### 2.2 Data Load
- Load Phase 1 ingestion data into Neo4j
- Validate: no dangling references, all relationships connected

#### 2.3 Query Testing
- Test all transitive dependency queries
- Verify circular dependency detection works
- Test impact analysis query

**Success Criteria**:
- ✓ Neo4j instance stable, <100ms query latency
- ✓ All Phase 1 data loaded
- ✓ Transitive queries work (A→B→C up to depth 5)
- ✓ Graph integrity validated

---

### PHASE 3: Impact Analysis Engine (Weeks 6–7)

#### 3.1 Implement 4 Rules
**Goal**: Given a change, find all affected services using Neo4j queries.

```python
class ImpactAnalysisEngine:
  def __init__(self, neo4j_client):
    self.neo4j = neo4j_client
  
  def analyze(self, change: Change) -> ImpactAnalysisResult:
    # Rule 1: Direct impact
    direct = self.rule_direct_impact(change)
    
    # Rule 2: Transitive impact (A→B→C)
    transitive = self.rule_transitive_impact(change)
    
    # Rule 3: Schema impact
    schema = self.rule_schema_impact(change)
    
    # Rule 4: Reverse dependency
    reverse = self.rule_reverse_dependency(change)
    
    affected = direct ∪ transitive ∪ schema ∪ reverse
    
    return ImpactAnalysisResult(
      affectedServices=affected,
      impactChains=self.compute_impact_chains(affected),
      breakingChanges=change.breakingChanges
    )
  
  def rule_direct_impact(self, change: Change) -> Set[Service]:
    # Find all services that call the changed service
    query = """
    MATCH (changed:Service {id: $serviceId})-[:INITIATES]->
          (conn:ExternalConnection)-[:TARGETS]->(affected:Service)
    RETURN affected
    """
    return self.neo4j.query(query, serviceId=change.repoId)
  
  def rule_transitive_impact(self, change: Change) -> Set[Service]:
    # Find services reachable via transitive calls
    query = """
    MATCH (changed:Service {id: $serviceId})-[:INITIATES]->
          (conn1:ExternalConnection)-[:TARGETS]->(mid:Service),
          (mid)-[:INITIATES]->
          (conn2:ExternalConnection)-[:TARGETS]->(final:Service)
    RETURN DISTINCT final
    UNION
    MATCH (changed:Service {id: $serviceId})-[:INITIATES]->
          (conn1:ExternalConnection)-[:TARGETS]->(mid1:Service),
          (mid1)-[:INITIATES]->
          (conn2:ExternalConnection)-[:TARGETS]->(mid2:Service),
          (mid2)-[:INITIATES]->
          (conn3:ExternalConnection)-[:TARGETS]->(final:Service)
    RETURN DISTINCT final
    """
    return self.neo4j.query(query, serviceId=change.repoId)
  
  def rule_schema_impact(self, change: Change) -> Set[Service]:
    # Find services using changed schema
    query = """
    MATCH (changed:Service {id: $serviceId})-[:DEFINES]->(schema:SchemaDefinition),
          (conn:ExternalConnection)-[:USES_SCHEMA]->(schema),
          (conn)-[:TARGETS]->(affected:Service)
    RETURN DISTINCT affected
    """
    return self.neo4j.query(query, serviceId=change.repoId)
  
  def rule_reverse_dependency(self, change: Change) -> Set[Service]:
    # Find services that depend on the changed service
    query = """
    MATCH (changed:Service {id: $serviceId})<-[:TARGETS]-
          (conn:ExternalConnection)<-[:INITIATES]-(dependent:Service)
    RETURN DISTINCT dependent
    """
    return self.neo4j.query(query, serviceId=change.repoId)
  
  def compute_impact_chains(self, affected_services: Set[Service]) -> List[List[Service]]:
    # Return the actual paths A→B→C for visualization
    chains = []
    for service in affected_services:
      query = """
      MATCH path = (changed:Service {id: $serviceId})-[:INITIATES]->
                   (conn:ExternalConnection)-[:TARGETS]*->(target:Service)
      WHERE target.id = $targetId
      RETURN path
      """
      path = self.neo4j.query(query, serviceId=change.repoId, targetId=service.id)
      chains.append(path)
    return chains
```

#### 3.2 Add Confidence Scoring
Each affected service gets a confidence score:
- **Direct impact**: 0.95 (traced from production)
- **1-hop transitive**: 0.85 (A calls B, B calls C, so A→C)
- **2-hop transitive**: 0.75 (A→B→C→D)
- **Schema impact**: 0.80 (shares schema, might break)
- **Reverse dependency**: 0.90 (depends on changed service, likely affected)

**Success Criteria**:
- ✓ All 4 rules implemented + tested
- ✓ Transitive queries correct (manual verification on 5+ repos)
- ✓ Circular dependencies detected
- ✓ Confidence scoring makes sense

---

### PHASE 4: Test Selection & Mapping (Weeks 8–9)

#### 4.1 Implement Test Selection Algorithm

```python
class TestSelector:
  def __init__(self, neo4j_client):
    self.neo4j = neo4j_client
  
  def select_tests(self, change: Change, impact: ImpactAnalysisResult) -> TestSelectionResult:
    selected_tests = []
    
    # Step 1: In changed service, run all UNIT + COMPONENT
    if change.repoId:
      query = """
      MATCH (service:Service {id: $serviceId})-[:DEFINES]->(test:TestCase)
      WHERE test.type IN ['UNIT', 'COMPONENT']
      RETURN test
      ORDER BY test.priority DESC, test.duration_ms ASC
      """
      tests = self.neo4j.query(query, serviceId=change.repoId)
      for test in tests:
        selected_tests.append((test, 'CRITICAL', 'changed repo'))
    
    # Step 2: In affected services, run INTEGRATION tests that touch changed service
    for affected_service in impact.affectedServices:
      query = """
      MATCH (service:Service {id: $serviceId})-[:DEFINES]->(test:TestCase),
            (test)-[:DEPENDS_ON]->(conn:ExternalConnection)-[:TARGETS]->(changed:Service {id: $changedId})
      WHERE test.type = 'INTEGRATION'
      RETURN test
      ORDER BY test.priority DESC, test.duration_ms ASC
      """
      tests = self.neo4j.query(query, serviceId=affected_service.id, changedId=change.repoId)
      for test in tests:
        selected_tests.append((test, 'HIGH', f'integration with {change.repoId}'))
    
    # Step 3: For breaking changes, run all tests in affected services
    if change.breakingChanges:
      for affected_service in impact.affectedServices:
        query = """
        MATCH (service:Service {id: $serviceId})-[:DEFINES]->(test:TestCase)
        RETURN test
        """
        tests = self.neo4j.query(query, serviceId=affected_service.id)
        for test in tests:
          if test not in [t[0] for t in selected_tests]:
            selected_tests.append((test, 'CRITICAL', 'breaking change'))
    
    # Step 4: Deduplicate + sort
    unique_tests = {test.id: (test, priority, reason) 
                   for test, priority, reason in selected_tests}
    
    sorted_tests = sorted(unique_tests.values(), 
                         key=lambda x: priority_weight(x[1]) + x[0].duration_ms)
    
    return TestSelectionResult(
      tests=sorted_tests,
      pyramid=self.build_pyramid(sorted_tests),
      estimatedDuration_ms=sum(t[0].duration_ms for t in sorted_tests)
    )
  
  def build_pyramid(self, tests: List[TestCase]) -> TestPyramid:
    # Organize tests by tier
    unit = [t for t in tests if t.type == 'UNIT']
    component = [t for t in tests if t.type == 'COMPONENT']
    integration = [t for t in tests if t.type == 'INTEGRATION']
    
    return TestPyramid(
      tier1_unit=unit,
      tier2_component=component,
      tier3_integration=integration,
      parallel_within_tier=True,
      serial_across_tiers=True
    )
```

#### 4.2 Validate Test Selection
- Run on real changes, compare selected tests vs. actual test failures
- Measure false negatives (tests we should have run but didn't)
- Measure false positives (tests we ran but wouldn't have failed)

**Success Criteria**:
- ✓ <10% false negatives (tests that fail but weren't selected)
- ✓ <20% false positives (tests that pass but were selected)
- ✓ 50% reduction in CI time vs. running all tests

---

### PHASE 5: CI Integration (Weeks 10–11)

#### 5.1 Build API Server
```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class ChangeRequest(BaseModel):
  repoId: str
  commitHash: str
  files: List[str]

@app.post("/api/analyze")
async def analyze_impact(change: ChangeRequest):
  # Create Change object
  change_obj = Change(
    id=change.commitHash,
    repoId=change.repoId,
    files=change.files
  )
  
  # Run impact analysis
  engine = ImpactAnalysisEngine(neo4j)
  impact = engine.analyze(change_obj)
  
  # Select tests
  selector = TestSelector(neo4j)
  tests = selector.select_tests(change_obj, impact)
  
  return {
    'impact': impact,
    'selectedTests': tests,
    'estimatedDuration_ms': tests.estimatedDuration_ms,
    'pyramid': tests.pyramid
  }
```

#### 5.2 GitHub Actions Integration
```yaml
name: Smart Test Selection

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  impact-analysis:
    runs-on: ubuntu-latest
    outputs:
      tests: ${{ steps.analyze.outputs.tests }}
      duration: ${{ steps.analyze.outputs.duration }}
    steps:
      - uses: actions/checkout@v3
      
      - name: Analyze impact
        id: analyze
        run: |
          RESPONSE=$(curl -X POST http://impact-api:8000/api/analyze \
            -d '{
              "repoId": "${{ github.event.pull_request.head.repo.name }}",
              "commitHash": "${{ github.event.pull_request.head.sha }}",
              "files": $(git diff --name-only origin/main...HEAD | jq -R -s 'split("\n")[:-1]')
            }')
          echo "tests=$(echo $RESPONSE | jq .selectedTests)" >> $GITHUB_OUTPUT
          echo "duration=$(echo $RESPONSE | jq .estimatedDuration_ms)" >> $GITHUB_OUTPUT
      
      - name: Run selected tests
        run: |
          # Parse test list and run
          echo "${{ steps.analyze.outputs.tests }}" | \
            jq -r '.[] | .id' | \
            xargs pytest -v
```

#### 5.3 Feedback Loop
- Store test execution results in Neo4j
- Update TestCase.flakiness_score based on pass/fail history
- Alert if impact analysis misses tests that actually fail

**Success Criteria**:
- ✓ API responds <500ms
- ✓ GitHub Actions integration working
- ✓ CI time reduced by 50–80%
- ✓ False negative rate <10%

---

### PHASE 6: Observability & Feedback (Weeks 12–13)

#### 6.1 Monitoring

```python
# Track impact analysis accuracy
@app.get("/api/stats")
async def get_stats():
  stats = {
    'total_changes_analyzed': count(),
    'avg_affected_services': average(),
    'avg_tests_selected': average(),
    'avg_ci_time_before': 300000,  # 5 min
    'avg_ci_time_after': 60000,    # 1 min
    'false_positive_rate': 0.15,
    'false_negative_rate': 0.08,
    'circular_dependencies': count(),
    'stale_connections': [...]
  }
  return stats
```

#### 6.2 Dashboard
- Service dependency graph (interactive Neo4j visualization)
- Impact chains (A→B→C with edge weights)
- Test coverage heatmap
- CI time trends
- Circular dependency alerts

#### 6.3 Continuous Sync
- Re-ingest Datadog traces every 12 hours
- Re-parse test files on every merge
- Re-extract code artifacts on every merge
- Update connection frequency/criticality

**Success Criteria**:
- ✓ Dashboard operational
- ✓ Monitoring alerts working
- ✓ False negative/positive rates stable
- ✓ Team confidence in system

---

## Part 5: Complete System Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                     COMPLETE MULTI-REPO IMPACT ANALYSIS SYSTEM                             │
└──────────────────────────────────────────────────────────────────────────────────────────────┘

                             GIT REPOSITORIES (Polyrepo)
                          ┌────────────────────────────┐
                          │ auth-service / payment-service / ...
                          │ (7 services total)
                          └────────────────────────────┘
                                      │
                ┌───────────────┬─────┴─────┬───────────────┐
                ▼               ▼           ▼               ▼
         ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
         │ Static       │ │ Test Files   │ │ Datadog      │
         │ Analysis     │ │ Parsing      │ │ APM Traces   │
         │ (Phase 1)    │ │ (Phase 1)    │ │ (Phase 1)    │
         └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
                │                │                │
                ├────────────────┼────────────────┤
                │                │                │
                ▼                ▼                ▼
         CodeArtifacts    TestCases        ExternalConnections
         (Functions,      (Unit/Comp/       (Service calls,
         Endpoints,       Integration)      DB queries,
         Schemas)                           Message topics)
                │                │                │
                └────────────────┼────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Neo4j Graph Database  │
                    │                        │
                    │  Service ─INITIATES──► │
                    │   │      ExternalConn  │
                    │   │        │            │
                    │   │        ├──TARGETS──┤
                    │   │                     │
                    │   ├──DEFINES─►          │
                    │   │      TestCase      │
                    │   │        │            │
                    │   │        ├──COVERS──►
                    │   │        │ CodeArtifact
                    │   │        │            │
                    │   │        ├──DEPENDS──►
                    │   │                     │
                    │   ├──CONTAINS──►        │
                    │        CodeArtifact     │
                    │          │              │
                    │          └──EXPOSES──► │
                    │                        │
                    │  + SchemaDefinition    │
                    │  + ExternalResource    │
                    │  + Change              │
                    └────────────┬───────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │ IMPACT ANALYSIS ENGINE   │
                    │                         │
                    │ Rule 1: Direct Impact   │
                    │ Rule 2: Transitive      │
                    │ Rule 3: Schema Impact   │
                    │ Rule 4: Reverse Dep     │
                    │                         │
                    │ Query: (A→B→C→D...)    │
                    │ Output: Affected        │
                    │        services +       │
                    │        confidence       │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  TEST SELECTOR           │
                    │                         │
                    │ Filter tests by:        │
                    │ • Affected service      │
                    │ • Test type (pyramid)   │
                    │ • Priority/duration     │
                    │                         │
                    │ Output: Tier 1, 2, 3   │
                    │        tests + time     │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   API SERVER            │
                    │   POST /api/analyze     │
                    │                        │
                    │ Input: Change object   │
                    │ Output: Test list,     │
                    │        duration,       │
                    │        pyramid         │
                    └────────────┬───────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  CI INTEGRATION          │
                    │                         │
                    │  GitHub Actions /       │
                    │  Jenkins / GitLab CI    │
                    │                         │
                    │  Tier 1 (UNIT):         │
                    │    ├─ test-jwt-gen      │
                    │    ├─ test-jwt-verify   │
                    │    └─ ... (parallel)    │
                    │                         │
                    │  Tier 2 (COMPONENT):    │
                    │    └─ ... (parallel)    │
                    │                         │
                    │  Tier 3 (INTEGRATION):  │
                    │    └─ ... (parallel)    │
                    │                         │
                    │  Result: PASS/FAIL      │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  FEEDBACK LOOP           │
                    │                         │
                    │ • Store results in DB   │
                    │ • Update flakiness      │
                    │ • Track false pos/neg   │
                    │ • Alert on stale conns  │
                    │ • Improve rules         │
                    └─────────────────────────┘
                                 │
                    ┌────────────▼──────────────┐
                    │  OBSERVABILITY DASHBOARD  │
                    │                          │
                    │  • Dep graph visual      │
                    │  • Impact chains         │
                    │  • Test coverage        │
                    │  • CI time trends       │
                    │  • False pos/neg rates   │
                    │  • Circular deps        │
                    │  • Stale connections    │
                    └──────────────────────────┘
```

---

## Part 6: Timeline & Deliverables

| Week | Phase | Deliverable | Owner |
|------|-------|-------------|-------|
| 1-3 | Phase 1 | Datadog traces, test files, code artifacts ingested | Data Engineer |
| 4-5 | Phase 2 | Neo4j graph populated, queries tested | Database Engineer |
| 6-7 | Phase 3 | Impact analysis engine (4 rules) | Backend Engineer |
| 8-9 | Phase 4 | Test selection algorithm, >50% CI time reduction | QA Engineer |
| 10-11 | Phase 5 | API server, GitHub Actions integration | DevOps Engineer |
| 12-13 | Phase 6 | Dashboard, monitoring, feedback loop | DevOps + Data |

---

## Part 7: Success Metrics

**By end of Phase 6**:
- ✅ CI time: 5 min → 1 min average (80% reduction)
- ✅ False negative rate: <10% (missed tests)
- ✅ False positive rate: <20% (unnecessary tests)
- ✅ Transitive dependencies: all A→B→C chains detected
- ✅ Circular dependencies: all detected + alerted
- ✅ Team adoption: >80% of commits use smart test selection
- ✅ Stale connections: monitored + auto-alerted

---

## Part 8: High-Risk Items & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| Datadog traces incomplete | Medium | High | Sample 5 repos, validate coverage vs. code |
| Test categorization wrong | Medium | High | Manual review of 20 random tests |
| Neo4j query performance | Low | Medium | Index all node IDs, test on 100+ nodes |
| Circular dependencies | Low | Medium | Detect in Phase 2, alert team |
| False negatives in prod | Medium | High | Beta with 1 team first, monitor closely |
| Transitive depth explosion | Medium | Medium | Limit to depth 5, warn on long chains |

---

## Part 9: Dependencies & Constraints

**Must-have**:
- Neo4j instance (can be self-hosted or managed)
- Datadog APM enabled on all services
- Git webhooks for change detection
- CI system with API access (GitHub Actions, Jenkins, GitLab CI)

**Nice-to-have**:
- OpenTelemetry integration (more detailed traces)
- Slack notifications for stale connections
- Automated schema diffing tool
- Machine learning for confidence scoring refinement

---

## Conclusion

This system is built in **phases**, with **Neo4j as the core**. Each phase builds on the previous, enabling you to:
1. Discover real dependencies (Datadog)
2. Build queryable graph (Neo4j)
3. Find transitive impacts (A→B→C)
4. Select minimal tests (smart pyramid)
5. Speed up CI (from 5 min → 1 min)
6. Monitor & iterate (observability)

**Start with Phase 1 & 2** (ingestion + graph). Everything else flows from there.
