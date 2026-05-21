import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import GraphCanvas, { GraphLayout } from "../components/GraphCanvas";
import SearchBox from "../components/SearchBox";
import NodeDetail from "../components/NodeDetail";
import ImpactPanel from "../components/ImpactPanel";
import { useGraphOverview, useImpact, useNeighborhood, useNode } from "../api/hooks";
import type { GraphEdge, GraphNode } from "../api/types";

type RightTab = "details" | "impact";

// The explorer is a three-column layout:
//   * left:   search + a quick list of services for navigation
//   * center: Cytoscape canvas
//   * right:  details for the selected node, impact analysis, report download
//
// Picking a node from anywhere is the same path: setSelected(id), which
// triggers all the dependent queries.
export default function Explorer() {
  const { nodeId: routeNodeId } = useParams();
  const navigate = useNavigate();

  const overview = useGraphOverview();
  const [selectedId, setSelectedIdState] = useState<string | null>(routeNodeId ?? null);
  const [rightTab, setRightTab] = useState<RightTab>("details");
  const [layout, setLayout] = useState<GraphLayout>("dagre");
  const [highlightIds, setHighlightIds] = useState<Set<string> | null>(null);
  const [impactDirection, setImpactDirection] = useState<"downstream" | "upstream">(
    "downstream",
  );
  const [impactDepth, setImpactDepth] = useState(3);

  const setSelected = useCallback(
    (id: string | null) => {
      setSelectedIdState(id);
      setHighlightIds(null);
      if (id) {
        navigate(`/explore/${encodeURIComponent(id)}`, { replace: true });
      } else {
        navigate(`/explore`, { replace: true });
      }
    },
    [navigate],
  );

  // Keep state in sync if the route is changed externally (deep link).
  useEffect(() => {
    if (routeNodeId && routeNodeId !== selectedId) {
      setSelectedIdState(routeNodeId);
    }
  }, [routeNodeId, selectedId]);

  const selectedNode = useNode(selectedId);
  const neighborhood = useNeighborhood(rightTab === "details" ? selectedId : null, 1);
  const impact = useImpact(
    rightTab === "impact" ? selectedId : null,
    impactDirection,
    impactDepth,
  );

  // Decide what the canvas renders:
  // 1. When impact mode is active AND we have data, show the impact
  //    subgraph (root + reachable nodes + the edges among them).
  // 2. Else when a node is selected, show its 1-hop neighborhood so
  //    you can see immediate context.
  // 3. Else show the service overview.
  const canvasGraph: { nodes: GraphNode[]; edges: GraphEdge[] } = useMemo(() => {
    if (rightTab === "impact" && impact.data) {
      const nodes: GraphNode[] = [
        impact.data.root,
        ...impact.data.nodes.map((n) => ({
          id: n.id,
          kind: n.kind,
          type: n.type,
          name: n.name,
          repo_id: n.repo_id,
          file: n.file,
          language: "",
          framework: "",
        })),
      ];
      return { nodes, edges: impact.data.edges };
    }
    if (selectedId && neighborhood.data && neighborhood.data.nodes.length > 0) {
      return { nodes: neighborhood.data.nodes, edges: neighborhood.data.edges };
    }
    return {
      nodes: overview.data?.nodes ?? [],
      edges: overview.data?.edges ?? [],
    };
  }, [rightTab, impact.data, selectedId, neighborhood.data, overview.data]);

  return (
    <div className="grid grid-cols-[280px_1fr_360px] h-full">
      {/* ---- Left rail ---- */}
      <aside className="border-r border-slate-700 p-3 flex flex-col gap-3 min-h-0">
        <SearchBox
          onPick={(hit) => {
            setRightTab("details");
            setSelected(hit.id);
          }}
        />
        <div>
          <div className="text-xs uppercase tracking-wider text-slate-500 mb-1">
            Services
          </div>
          <ServiceList
            nodes={overview.data?.nodes ?? []}
            selectedId={selectedId}
            onPick={(id) => {
              setRightTab("details");
              setSelected(id);
            }}
          />
        </div>
      </aside>

      {/* ---- Center: canvas ---- */}
      <section className="relative min-w-0">
        <div className="absolute top-2 left-2 z-10 flex gap-2 items-center bg-slate-900/70 backdrop-blur rounded-md border border-slate-700 px-2 py-1">
          <label className="text-xs text-slate-400">layout</label>
          <select
            value={layout}
            onChange={(e) => setLayout(e.target.value as GraphLayout)}
            className="bg-slate-800 border border-slate-700 rounded px-1.5 py-0.5 text-xs"
          >
            <option value="dagre">dagre (hierarchical)</option>
            <option value="cose">cose (force)</option>
            <option value="breadthfirst">breadthfirst</option>
          </select>
          <span className="text-xs text-slate-500 ml-2">
            {canvasGraph.nodes.length} nodes • {canvasGraph.edges.length} edges
          </span>
        </div>
        <GraphCanvas
          nodes={canvasGraph.nodes}
          edges={canvasGraph.edges}
          selectedId={selectedId}
          highlightedIds={highlightIds ?? undefined}
          layout={layout}
          onSelect={(id) => setSelected(id)}
        />
        <Legend />
      </section>

      {/* ---- Right rail ---- */}
      <aside className="border-l border-slate-700 p-3 flex flex-col gap-3 min-h-0">
        <div className="flex gap-1 border-b border-slate-700">
          <TabButton active={rightTab === "details"} onClick={() => setRightTab("details")}>
            Details
          </TabButton>
          <TabButton active={rightTab === "impact"} onClick={() => setRightTab("impact")}>
            Impact
          </TabButton>
        </div>
        <div className="min-h-0 flex-1 overflow-auto pr-1">
          {rightTab === "details" ? (
            <NodeDetail node={selectedNode.data ?? null} />
          ) : (
            <ImpactPanel
              nodeId={selectedId}
              onHighlight={setHighlightIds}
              direction={impactDirection}
              depth={impactDepth}
              onChangeDirection={setImpactDirection}
              onChangeDepth={setImpactDepth}
            />
          )}
        </div>
      </aside>
    </div>
  );
}

function ServiceList({
  nodes,
  selectedId,
  onPick,
}: {
  nodes: GraphNode[];
  selectedId: string | null;
  onPick: (id: string) => void;
}) {
  if (nodes.length === 0) {
    return (
      <div className="text-xs text-slate-500 italic">
        No services in the graph yet. Run <code>sg-ingest</code> to populate it.
      </div>
    );
  }
  return (
    <ul className="space-y-0.5 max-h-[calc(100vh-220px)] overflow-auto pr-1">
      {nodes.map((n) => (
        <li key={n.id}>
          <button
            className={`w-full text-left text-sm px-2 py-1 rounded hover:bg-slate-800 ${
              selectedId === n.id ? "bg-slate-800 text-white" : "text-slate-300"
            }`}
            onClick={() => onPick(n.id)}
          >
            <div className="truncate">{n.name || n.id}</div>
            <div className="text-[10px] text-slate-500 truncate">
              {n.language || n.kind}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      className={`px-3 py-1.5 text-sm border-b-2 -mb-px ${
        active
          ? "border-accent text-white"
          : "border-transparent text-slate-400 hover:text-white"
      }`}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function Legend() {
  const items: Array<[string, string]> = [
    ["Service", "#6ea8ff"],
    ["CodeArtifact", "#7ee787"],
    ["TestCase", "#f0883e"],
    ["ExternalConnection", "#bc8cff"],
    ["Endpoint", "#79c0ff"],
    ["Kafka*", "#ffa657"],
  ];
  return (
    <div className="absolute bottom-2 left-2 z-10 bg-slate-900/70 backdrop-blur rounded-md border border-slate-700 px-2 py-1 text-xs flex gap-3">
      {items.map(([label, color]) => (
        <div key={label} className="flex items-center gap-1">
          <span
            className="inline-block w-2.5 h-2.5 rounded-full"
            style={{ backgroundColor: color }}
          />
          <span className="text-slate-300">{label}</span>
        </div>
      ))}
    </div>
  );
}
