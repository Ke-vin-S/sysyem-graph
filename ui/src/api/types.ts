// Mirror of api/schemas/*. Kept hand-rolled because the API surface is
// small and the schema is unlikely to drift. If the API grows, we can
// generate this from /openapi.json.

export interface GraphNode {
  id: string;
  kind: string;
  type: string;
  name: string;
  repo_id: string;
  file: string;
  language: string;
  framework: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  rel: string;
}

export interface GraphSubgraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphCountsResponse {
  services: number;
  artifacts: number;
  tests: number;
  connections: number;
  edges: Record<string, number>;
}

export interface SearchHit {
  id: string;
  kind: string;
  name: string;
  repo_id: string;
  file: string;
}

export interface ImpactNode {
  id: string;
  kind: string;
  type: string;
  name: string;
  repo_id: string;
  file: string;
  depth: number;
  via_rels: string[];
}

export interface ImpactResponse {
  root: GraphNode;
  direction: "downstream" | "upstream";
  depth: number;
  nodes: ImpactNode[];
  edges: GraphEdge[];
}

export interface PipelineSummary {
  id: string;
  label: string;
  enabled: boolean;
  status: string;
  last_ran_at: string;
  detail: string;
  config: Record<string, string>;
}

export interface GitHubRepoState {
  url: string;
  owner: string;
  name: string;
  status: string;
  last_commit_sha: string;
  last_ingested_at: string;
  last_ingested_sha: string;
  last_error: string;
}

export interface GitHubPipelineDetail extends PipelineSummary {
  repos: GitHubRepoState[];
}

export interface DatadogPipelineDetail extends PipelineSummary {
  spans_count: number;
  services_count: number;
  spans_last_fetched_at: string;
  catalog_last_fetched_at: string;
}

export interface TestParserPipelineDetail extends PipelineSummary {
  root: string;
  single_repo: boolean | null;
  exists: boolean;
}

export interface PipelinesResponse {
  pipelines: PipelineSummary[];
}

export interface Neo4jHealth {
  reachable: boolean;
  uri: string;
  database: string;
}

export interface ImpactReportRequest {
  node_id: string;
  direction: "downstream" | "upstream";
  depth: number;
  title?: string;
}

export interface ImpactReportResponse {
  title: string;
  markdown: string;
  generated_at: string;
  node_count: number;
}
