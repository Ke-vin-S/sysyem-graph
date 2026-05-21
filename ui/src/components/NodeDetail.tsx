import type { GraphNode } from "../api/types";

interface NodeDetailProps {
  node: GraphNode | null | undefined;
}

// Right-panel summary of the focused node. The impact data lives in
// `ImpactPanel`; this is just the identity card.
export default function NodeDetail({ node }: NodeDetailProps) {
  if (!node) {
    return (
      <div className="text-sm text-slate-400">
        Select a node to see details. Search by name or ID, or click a node in the graph.
      </div>
    );
  }
  const rows: Array<[string, string]> = [
    ["kind", node.kind],
    ["type", node.type],
    ["repo", node.repo_id],
    ["file", node.file],
    ["language", node.language],
    ["framework", node.framework],
  ].filter(([, v]) => Boolean(v)) as Array<[string, string]>;

  return (
    <div>
      <div className="text-lg font-semibold break-all">{node.name || node.id}</div>
      <div className="text-xs text-slate-500 break-all mb-3">{node.id}</div>
      <div className="grid grid-cols-3 gap-x-2 gap-y-1 text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <div className="text-slate-400">{k}</div>
            <div className="col-span-2 text-slate-200 break-words">{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
