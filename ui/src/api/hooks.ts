// React Query hooks. Keyed on the URL itself so query-keys stay
// trivially debuggable in devtools.

import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type {
  GraphCountsResponse,
  GraphNode,
  GraphSubgraph,
  ImpactReportRequest,
  ImpactReportResponse,
  ImpactResponse,
  Neo4jHealth,
  PipelinesResponse,
  SearchHit,
} from "./types";

export function useNeo4jHealth() {
  return useQuery({
    queryKey: ["/health/neo4j"],
    queryFn: () => api.get<Neo4jHealth>("/health/neo4j"),
    refetchInterval: 30_000,
  });
}

export function useGraphCounts() {
  return useQuery({
    queryKey: ["/api/graph/counts"],
    queryFn: () => api.get<GraphCountsResponse>("/api/graph/counts"),
  });
}

export function useGraphOverview() {
  return useQuery({
    queryKey: ["/api/graph/overview"],
    queryFn: () => api.get<GraphSubgraph>("/api/graph/overview"),
  });
}

export function useNode(nodeId: string | null | undefined) {
  return useQuery({
    queryKey: ["/api/graph/nodes", nodeId],
    queryFn: () => api.get<GraphNode>(`/api/graph/nodes/${encodeURIComponent(nodeId!)}`),
    enabled: Boolean(nodeId),
  });
}

export function useNeighborhood(nodeId: string | null | undefined, depth: number) {
  return useQuery({
    queryKey: ["/api/graph/neighborhood", nodeId, depth],
    queryFn: () =>
      api.get<GraphSubgraph>(
        `/api/graph/nodes/${encodeURIComponent(nodeId!)}/neighborhood?depth=${depth}`,
      ),
    enabled: Boolean(nodeId),
  });
}

export function useImpact(
  nodeId: string | null | undefined,
  direction: "downstream" | "upstream",
  depth: number,
) {
  return useQuery({
    queryKey: ["/api/graph/impact", nodeId, direction, depth],
    queryFn: () =>
      api.get<ImpactResponse>(
        `/api/graph/nodes/${encodeURIComponent(nodeId!)}/impact?direction=${direction}&depth=${depth}`,
      ),
    enabled: Boolean(nodeId),
  });
}

export function useSearch(query: string) {
  return useQuery({
    queryKey: ["/api/graph/search", query],
    queryFn: () =>
      api.get<SearchHit[]>(
        `/api/graph/search?q=${encodeURIComponent(query)}&limit=25`,
      ),
    enabled: query.trim().length > 0,
  });
}

export function usePipelines() {
  return useQuery({
    queryKey: ["/api/pipelines"],
    queryFn: () => api.get<PipelinesResponse>("/api/pipelines"),
  });
}

export function useGenerateReport() {
  return useMutation({
    mutationFn: (req: ImpactReportRequest) =>
      api.post<ImpactReportResponse>("/api/reports/impact", req),
  });
}
