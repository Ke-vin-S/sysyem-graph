# Extensible Ingestion Framework
## Pluggable Adapters, Custom Implementations, Self-Improving

**Purpose**: Allow teams to enhance mapping quality incrementally without modifying core system.

---

## Part 1: Core Ingestion Interface

### 1.1 Abstract Ingestion Adapter

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class Service:
  id: str
  name: str
  repoUrl: str
  language: str
  framework: str

@dataclass
class ExternalConnection:
  id: str
  sourceServiceId: str
  targetServiceId: Optional[str]
  targetName: str
  type: str  # HTTP_CALL, DATABASE, MESSAGE_QUEUE, etc
  endpoint: str
  frequency: float
  criticality: str
  discoveredAt: str

@dataclass
class CodeArtifact:
  id: str
  repoId: str
  type: str  # FUNCTION, HTTP_ENDPOINT, SCHEMA, etc
  name: str
  file: str
  externalConnections: List[str]

@dataclass
class TestCase:
  id: str
  repoId: str
  type: str  # UNIT, COMPONENT, INTEGRATION
  name: str
  file: str
  affectedRepos: List[str]

class IngestionAdapter(ABC):
  """
  Base class for any data source that feeds the mapping system.
  Implementations: DatadogAdapter, GitRepoAdapter, TestParser, DocParser, etc.
  """
  
  @abstractmethod
  def get_identifier(self) -> str:
    """Unique ID for this adapter (e.g., 'datadog-apm', 'github-code', 'doc-parser')"""
    pass
  
  @abstractmethod
  def get_version(self) -> str:
    """Semantic version (for tracking schema changes)"""
    pass
  
  @abstractmethod
  def extract(self, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main extraction method.
    
    Args:
      config: Adapter-specific config (API keys, repo paths, etc)
    
    Returns:
      {
        'services': [Service, ...],
        'connections': [ExternalConnection, ...],
        'artifacts': [CodeArtifact, ...],
        'tests': [TestCase, ...],
        'metadata': {
          'timestamp': ISO8601,
          'source': 'datadog-apm',
          'coverage': 0.92,
          'warnings': ['...']
        }
      }
    """
    pass
  
  @abstractmethod
  def validate(self, extracted_data: Dict) -> tuple[bool, List[str]]:
    """
    Validate extracted data for consistency.
    
    Returns: (is_valid: bool, errors: List[str])
    """
    pass
  
  @abstractmethod
  def get_coverage(self, extracted_data: Dict) -> Dict[str, float]:
    """
    Report coverage metrics.
    
    Returns: {
      'services_covered': 0.95,
      'connections_captured': 0.87,
      'tests_classified': 0.98,
      'confidence_avg': 0.90
    }
    """
    pass


class CustomIngestionAdapter(IngestionAdapter):
  """Example: Custom adapter for team-specific logic"""
  
  def get_identifier(self) -> str:
    return "custom-documentation-parser"
  
  def get_version(self) -> str:
    return "1.0.0"
  
  def extract(self, config: Dict) -> Dict:
    # Custom logic: read ARCHITECTURE.md, README.md, design docs
    # Extract service descriptions, undocumented dependencies
    # Return structured data
    pass
  
  def validate(self, data: Dict) -> tuple[bool, List[str]]:
    # Custom validation: ensure all services have descriptions
    # Check for malformed connection endpoints
    pass
  
  def get_coverage(self) -> Dict:
    # Report: "Found 3 undocumented connections via docs"
    pass
```

---

## Part 2: Pluggable Adapter Registry

### 2.1 Adapter Manager (Core System)

```python
class AdapterRegistry:
  """Manages all registered ingestion adapters"""
  
  def __init__(self):
    self.adapters: Dict[str, IngestionAdapter] = {}
    self.execution_order: List[str] = []
  
  def register(self, adapter: IngestionAdapter, 
               priority: int = 100,
               enabled: bool = True) -> None:
    """
    Register a new adapter.
    
    Args:
      adapter: IngestionAdapter implementation
      priority: Execution order (lower = earlier)
      enabled: Run this adapter by default
    """
    adapter_id = adapter.get_identifier()
    self.adapters[adapter_id] = {
      'adapter': adapter,
      'priority': priority,
      'enabled': enabled,
      'version': adapter.get_version()
    }
    self._sort_by_priority()
  
  def unregister(self, adapter_id: str) -> None:
    """Remove adapter"""
    del self.adapters[adapter_id]
  
  def enable(self, adapter_id: str) -> None:
    """Activate adapter"""
    self.adapters[adapter_id]['enabled'] = True
  
  def disable(self, adapter_id: str) -> None:
    """Deactivate adapter (keep it registered)"""
    self.adapters[adapter_id]['enabled'] = False
  
  def run_all(self, config: Dict[str, Any]) -> IngestionResult:
    """
    Execute all enabled adapters in order, merge results.
    
    Flow:
      1. Run adapters sequentially (by priority)
      2. Each adapter extracts data
      3. Merge results (handle conflicts)
      4. Validate final output
      5. Return IngestionResult with metadata
    """
    results = {}
    metadata = {
      'adapters_run': [],
      'adapters_failed': [],
      'timestamp': now(),
      'total_services': 0,
      'total_connections': 0,
      'coverage': {}
    }
    
    for adapter_id in self.execution_order:
      if not self.adapters[adapter_id]['enabled']:
        continue
      
      adapter = self.adapters[adapter_id]['adapter']
      config_for_adapter = config.get(adapter_id, {})
      
      try:
        extracted = adapter.extract(config_for_adapter)
        
        # Validate before merging
        is_valid, errors = adapter.validate(extracted)
        if not is_valid:
          metadata['adapters_failed'].append({
            'adapter_id': adapter_id,
            'errors': errors
          })
          continue
        
        results[adapter_id] = extracted
        coverage = adapter.get_coverage(extracted)
        metadata['coverage'][adapter_id] = coverage
        metadata['adapters_run'].append(adapter_id)
        
      except Exception as e:
        metadata['adapters_failed'].append({
          'adapter_id': adapter_id,
          'error': str(e)
        })
    
    # Merge all results
    final_data = self._merge_results(results)
    
    return IngestionResult(
      data=final_data,
      metadata=metadata
    )
  
  def _merge_results(self, results: Dict[str, Dict]) -> Dict:
    """
    Merge outputs from multiple adapters.
    
    Strategy:
      • Services: Deduplicate by ID, union properties
      • Connections: Deduplicate by (source, target, endpoint)
      • Artifacts: Deduplicate by ID, merge coverage info
      • Tests: Deduplicate by ID, merge dependencies
    """
    merged = {
      'services': {},
      'connections': {},
      'artifacts': {},
      'tests': {}
    }
    
    for adapter_id, data in results.items():
      # Services: union, keep most recent
      for svc in data.get('services', []):
        if svc['id'] not in merged['services']:
          merged['services'][svc['id']] = svc
        else:
          merged['services'][svc['id']] = {
            **merged['services'][svc['id']],
            **svc,
            'sources': [
              *merged['services'][svc['id']].get('sources', []),
              adapter_id
            ]
          }
      
      # Connections: deduplicate + merge frequency
      for conn in data.get('connections', []):
        conn_key = (conn['sourceServiceId'], conn['targetServiceId'], 
                    conn['endpoint'])
        if conn_key not in merged['connections']:
          merged['connections'][conn_key] = {**conn, 'sources': []}
        
        # Merge: average frequency, highest criticality, union sources
        existing = merged['connections'][conn_key]
        existing['frequency'] = (existing['frequency'] + conn['frequency']) / 2
        existing['criticality'] = max(existing['criticality'], conn['criticality'])
        existing['sources'].append(adapter_id)
      
      # Similar merge logic for artifacts, tests
      # ...
    
    return {
      'services': list(merged['services'].values()),
      'connections': list(merged['connections'].values()),
      'artifacts': list(merged['artifacts'].values()),
      'tests': list(merged['tests'].values())
    }
  
  def _sort_by_priority(self) -> None:
    """Sort adapters by priority"""
    self.execution_order = sorted(
      self.adapters.keys(),
      key=lambda x: self.adapters[x]['priority']
    )


@dataclass
class IngestionResult:
  data: Dict[str, Any]
  metadata: Dict[str, Any]
```

---

## Part 3: Built-In Adapters (Examples)

### 3.1 Datadog APM Adapter

```python
class DatadogAdapter(IngestionAdapter):
  
  def get_identifier(self) -> str:
    return "datadog-apm"
  
  def get_version(self) -> str:
    return "1.0.0"
  
  def extract(self, config: Dict) -> Dict:
    """Query Datadog API, extract trace data"""
    api_key = config.get('api_key')
    services = config.get('services', [])
    
    # Query Datadog
    connections = []
    for service in services:
      traces = datadog_api.get_traces(
        service=service,
        last_days=30
      )
      
      for trace in traces:
        for span in trace.spans:
          if span.target_service:
            connections.append(ExternalConnection(
              sourceServiceId=service,
              targetServiceId=span.target_service,
              endpoint=span.resource,
              frequency=span.call_count_per_minute,
              criticality=self._compute_criticality(span.error_rate)
            ))
    
    return {
      'services': [Service(id=s, name=s) for s in services],
      'connections': connections,
      'artifacts': [],
      'tests': []
    }
```

### 3.2 GitHub Code Parser Adapter

```python
class GitHubCodeAdapter(IngestionAdapter):
  
  def get_identifier(self) -> str:
    return "github-code-parser"
  
  def get_version(self) -> str:
    return "2.1.0"  # Tracks schema version
  
  def extract(self, config: Dict) -> Dict:
    """Parse repos, extract code artifacts"""
    repos = config.get('repos', [])
    
    artifacts = []
    for repo in repos:
      ast = parse_repo(repo)
      
      for item in ast.declarations:
        artifacts.append(CodeArtifact(
          id=item.qualified_name,
          repoId=repo,
          type=item.type,
          name=item.name,
          file=item.file,
          externalConnections=[]  # Linked later
        ))
    
    return {
      'services': [],
      'connections': [],
      'artifacts': artifacts,
      'tests': []
    }
```

### 3.3 Documentation Parser Adapter (NEW!)

```python
class DocumentationAdapter(IngestionAdapter):
  """
  Reads ARCHITECTURE.md, README.md, design docs
  Extracts manually-documented connections
  """
  
  def get_identifier(self) -> str:
    return "documentation-parser"
  
  def get_version(self) -> str:
    return "1.0.0"
  
  def extract(self, config: Dict) -> Dict:
    """Parse documentation files, extract connections"""
    repos = config.get('repos', [])
    doc_paths = config.get('doc_paths', [
      'ARCHITECTURE.md',
      'README.md',
      'docs/design/*.md'
    ])
    
    connections = []
    
    for repo in repos:
      for doc_path in doc_paths:
        content = read_file(f"{repo}/{doc_path}")
        
        # Extract patterns like:
        # "auth-service calls payment-service via POST /api/charges"
        # "order-service depends on user-db table: users"
        
        matches = re.findall(
          r'(\w+-\w+)\s+calls?\s+(\w+-\w+).*?(\S+)',
          content
        )
        
        for source, target, endpoint in matches:
          connections.append(ExternalConnection(
            sourceServiceId=source,
            targetServiceId=target,
            endpoint=endpoint,
            frequency=1.0,  # Assumed low frequency (not in traces)
            criticality='MEDIUM',  # Conservative
            discoveredAt=now()
          ))
    
    return {
      'services': [],
      'connections': connections,
      'artifacts': [],
      'tests': []
    }
```

### 3.4 OpenAPI/Protobuf Schema Adapter

```python
class SchemaAdapter(IngestionAdapter):
  """Reads OpenAPI specs, Protobuf files, GraphQL schemas"""
  
  def get_identifier(self) -> str:
    return "schema-parser"
  
  def extract(self, config: Dict) -> Dict:
    """Parse API definitions, extract endpoints + data models"""
    
    openapi_files = find_files('openapi.yaml', 'swagger.json')
    protobuf_files = find_files('*.proto')
    
    artifacts = []
    connections = []
    
    # Parse OpenAPI
    for openapi_file in openapi_files:
      spec = parse_openapi(openapi_file)
      
      for path, methods in spec.paths.items():
        for method, op in methods.items():
          artifacts.append(CodeArtifact(
            id=f"{method.upper()}-{path}",
            type='HTTP_ENDPOINT',
            name=f"{method.upper()} {path}",
            file=openapi_file
          ))
      
      # Extract external dependencies from spec
      for external_ref in spec.external_refs:
        connections.append(ExternalConnection(
          targetName=external_ref,
          type='EXTERNAL_API',
          endpoint=external_ref
        ))
    
    # Similar for Protobuf, GraphQL...
    
    return {
      'services': [],
      'connections': connections,
      'artifacts': artifacts,
      'tests': []
    }
```

---

## Part 4: Configuration & Extensibility

### 4.1 Ingestion Config (YAML)

```yaml
# ingestion.yaml
adapters:
  datadog-apm:
    enabled: true
    priority: 1  # Run first
    config:
      api_key: ${DATADOG_API_KEY}
      services: ['auth-service', 'payment-service', 'order-service']
      days: 30
  
  github-code-parser:
    enabled: true
    priority: 2
    config:
      repos:
        - path: github.com/company/auth-service
          language: go
        - path: github.com/company/payment-service
          language: python
  
  test-parser:
    enabled: true
    priority: 3
    config:
      patterns: ['test_*.py', '*_test.go', '*.spec.ts']
  
  documentation-parser:
    enabled: true
    priority: 4  # Run last, fill gaps
    config:
      repos: ['github.com/company/*']
      doc_paths:
        - 'ARCHITECTURE.md'
        - 'README.md'
        - 'docs/design/*.md'
  
  custom-adapter:
    enabled: false  # Disable until ready
    priority: 5
    config:
      custom_param: value

# Merge strategy
merge_strategy:
  connections:
    deduplicate_by: ['sourceServiceId', 'targetServiceId', 'endpoint']
    on_conflict: 'merge'  # average frequency, max criticality
  services:
    deduplicate_by: ['id']
    on_conflict: 'union'  # Keep most complete record

# Quality gates (fail if not met)
quality_gates:
  min_coverage: 0.85
  max_unmapped_tests: 0.10
  min_confidence: 0.70
```

---

## Part 5: Adding Custom Implementations

### 5.1 Team Implements Custom Adapter (Easy)

```python
# team_custom_adapter.py
from ingestion import IngestionAdapter, ExternalConnection

class SlackArchiveAdapter(IngestionAdapter):
  """Extract architecture discussions from Slack"""
  
  def get_identifier(self) -> str:
    return "slack-architecture-discussion"
  
  def get_version(self) -> str:
    return "1.0.0"
  
  def extract(self, config: Dict) -> Dict:
    slack_token = config.get('slack_token')
    channels = config.get('channels', ['#architecture'])
    
    connections = []
    
    # Query Slack API
    for channel in channels:
      messages = slack_client.get_channel_history(
        channel=channel,
        query='service calls OR depends on'
      )
      
      for msg in messages:
        # Parse natural language: "auth calls payment-service"
        parsed = self._parse_message(msg.text)
        if parsed:
          connections.append(parsed)
    
    return {
      'services': [],
      'connections': connections,
      'artifacts': [],
      'tests': []
    }
  
  def validate(self, data: Dict) -> tuple[bool, List[str]]:
    # Validate connections have required fields
    errors = []
    for conn in data['connections']:
      if not conn.get('targetServiceId'):
        errors.append(f"Connection missing targetServiceId: {conn}")
    
    return len(errors) == 0, errors
  
  def get_coverage(self, data: Dict) -> Dict:
    return {
      'connections_from_slack': len(data['connections']),
      'confidence': 0.60  # Natural language parsing is less confident
    }

# Register it
registry.register(
  SlackArchiveAdapter(),
  priority=6,
  enabled=False  # Opt-in, not default
)
```

### 5.2 Team Enhances Existing Adapter

```python
# Extend DatadogAdapter with custom logic
class DatadogAdapterPlus(DatadogAdapter):
  """Enhanced Datadog with company-specific filtering"""
  
  def extract(self, config: Dict) -> Dict:
    data = super().extract(config)
    
    # Filter out test traffic
    data['connections'] = [
      c for c in data['connections']
      if not self._is_test_traffic(c)
    ]
    
    # Add company-specific criticality scoring
    for conn in data['connections']:
      conn['criticality'] = self._score_criticality(conn)
    
    return data
  
  def _is_test_traffic(self, conn: ExternalConnection) -> bool:
    # Filter out staging, canary, A/B test traffic
    return 'test' in conn.sourceServiceId.lower()
  
  def _score_criticality(self, conn: ExternalConnection) -> str:
    # Company policy: payment calls are always CRITICAL
    if 'payment' in conn.targetServiceId:
      return 'CRITICAL'
    # SLA-based scoring
    if conn.frequency > 1000:
      return 'HIGH'
    return 'MEDIUM'

registry.register(
  DatadogAdapterPlus(),
  priority=1,
  enabled=True
)
```

---

## Part 6: Auto-Improvement Loop

### 6.1 Feedback Loop Updates Adapters

```python
class IngestionFeedbackLoop:
  """Learn from CI results to improve mapping quality"""
  
  def __init__(self, registry: AdapterRegistry, neo4j):
    self.registry = registry
    self.neo4j = neo4j
  
  def on_test_result(self, test_result: TestResult) -> None:
    """
    Called after test execution.
    
    If impact analysis was wrong:
      - Adjust confidence scores
      - Trigger adapter re-run
      - Update Neo4j
    """
    
    # Test was selected but passed → potential false positive
    if test_result.was_selected and test_result.passed:
      self._log_false_positive(test_result)
      
      # If consistent false positives on same connection:
      # Lower confidence for that connection
      conn = test_result.affected_connection
      if self._is_consistently_false_positive(conn):
        self.neo4j.adjust_confidence(conn, -0.1)
    
    # Test was NOT selected but failed → false negative
    if not test_result.was_selected and test_result.failed:
      self._log_false_negative(test_result)
      
      # Trigger re-ingestion to find missing connection
      self._trigger_reingestion(test_result.service)
  
  def _trigger_reingestion(self, service_id: str) -> None:
    """Re-run adapters for specific service"""
    config = self._get_config_for_service(service_id)
    
    # Run only relevant adapters
    result = self.registry.run_all(config)
    
    # Load into Neo4j, compare vs old data
    old_data = self.neo4j.get_service_data(service_id)
    new_data = result.data
    
    differences = self._diff(old_data, new_data)
    
    if differences:
      # Log and investigate
      print(f"Found missing connections: {differences}")
      self.neo4j.update_with_new_connections(differences)
```

---

## Part 7: Extensibility Examples

### What Teams Can Add

| Use Case | Adapter Type | Difficulty |
|----------|--------------|-----------|
| Extract from internal API catalog | Custom | Easy |
| Parse Kubernetes manifests (service mesh) | Custom | Easy |
| Read JIRA architecture labels | Custom | Easy |
| Query service registry (Consul, Eureka) | Custom | Easy |
| Parse API gateway logs (Kong, Traefik) | Custom | Medium |
| LLM-based doc summarization | Custom + LLM | Medium |
| Bi-directional sync with APM (CloudWatch, Elastic) | Custom | Medium |
| Real-time event streaming (Kafka topics list) | Custom | Hard |

### No Code Changes Needed

All extensions are **plugins**. Core system stays untouched.

---

## Summary: Extensibility Strategy

```
┌─────────────────────────────────────────────────────────┐
│ Extensible Ingestion Framework                          │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Registry (manages adapters)                            │
│   │                                                     │
│   ├─ DatadogAdapter (built-in)                         │
│   ├─ GitHubCodeAdapter (built-in)                      │
│   ├─ TestParser (built-in)                             │
│   ├─ DocumentationAdapter (built-in)                   │
│   ├─ SchemaAdapter (built-in)                          │
│   │                                                     │
│   ├─ CustomSlackAdapter (team-written)  ← Easy to add  │
│   ├─ CustomKubernetesAdapter (team-written)            │
│   └─ ... (unlimited)                                    │
│                                                         │
│ Execution:                                              │
│   1. Load config (enable/disable, priorities)           │
│   2. Run adapters in order                              │
│   3. Merge results (dedupe, conflict resolution)        │
│   4. Validate output                                    │
│   5. Load into Neo4j                                    │
│   6. Feedback loop (improve next run)                   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Result**: System grows with your needs. No rewrites. Teams own their adapters.
