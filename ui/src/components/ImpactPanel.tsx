import { useEffect } from "react";
import { useGenerateReport, useImpact } from "../api/hooks";
import type { ImpactNode } from "../api/types";

interface ImpactPanelProps {
  nodeId: string | null;
  direction: "downstream" | "upstream";
  depth: number;
  onChangeDirection: (d: "downstream" | "upstream") => void;
  onChangeDepth: (d: number) => void;
  onHighlight: (ids: Set<string> | null) => void;
}

// Side-list view of the impact analysis. Direction/depth state is
// hoisted into the parent (Explorer) so the canvas can read it without
// us round-tripping through React Query for the value.
export default function ImpactPanel({
  nodeId,
  direction,
  depth,
  onChangeDirection,
  onChangeDepth,
  onHighlight,
}: ImpactPanelProps) {
  const { data, isFetching, error } = useImpact(nodeId, direction, depth);
  const report = useGenerateReport();

  // When impact data arrives, push the affected IDs up so the canvas
  // dims everything else. Cleared on unmount or when the node changes.
  useEffect(() => {
    if (data && nodeId) {
      onHighlight(new Set<string>([nodeId, ...data.nodes.map((n) => n.id)]));
    } else {
      onHighlight(null);
    }
    return () => onHighlight(null);
  }, [data, nodeId, onHighlight]);

  function downloadReport() {
    if (!nodeId) return;
    report.mutate(
      { node_id: nodeId, direction, depth },
      {
        onSuccess: (resp) => {
          const blob = new Blob([resp.markdown], { type: "text/markdown" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `impact-${nodeId.replace(/[^a-z0-9-_]+/gi, "_")}.md`;
          a.click();
          URL.revokeObjectURL(url);
        },
      },
    );
  }

  if (!nodeId) {
    return (
      <div className="text-sm text-slate-400">
        Pick a node first — then this panel will show what changes if it does.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={direction}
          onChange={(e) =>
            onChangeDirection(e.target.value as "downstream" | "upstream")
          }
          className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm"
        >
          <option value="downstream">Downstream (what this depends on)</option>
          <option value="upstream">Upstream (callers of this)</option>
        </select>
        <label className="text-xs text-slate-400 flex items-center gap-1">
          depth
          <input
            type="number"
            min={1}
            max={8}
            value={depth}
            onChange={(e) => onChangeDepth(Math.max(1, Math.min(8, Number(e.target.value))))}
            className="w-12 bg-slate-800 border border-slate-700 rounded px-1 py-0.5 text-sm text-right"
          />
        </label>
        <button
          className="btn btn-primary ml-auto"
          onClick={downloadReport}
          disabled={!data || data.nodes.length === 0 || report.isPending}
        >
          {report.isPending ? "Generating…" : "Download report"}
        </button>
      </div>

      {error && (
        <div className="text-sm text-rose-400">
          Impact lookup failed: {String((error as Error).message ?? error)}
        </div>
      )}
      {isFetching && <div className="text-xs text-slate-400">Walking the graph…</div>}

      {data && (
        <>
          <div className="text-sm text-slate-300">
            <span className="font-semibold">{data.nodes.length}</span> affected node(s)
            within {data.depth} hop(s).
          </div>
          <ImpactList nodes={data.nodes} />
        </>
      )}
    </div>
  );
}

function ImpactList({ nodes }: { nodes: ImpactNode[] }) {
  if (nodes.length === 0) {
    return <div className="text-xs text-slate-500">No impacted nodes at this depth.</div>;
  }
  const byDepth = new Map<number, ImpactNode[]>();
  for (const n of nodes) {
    const bucket = byDepth.get(n.depth) ?? [];
    bucket.push(n);
    byDepth.set(n.depth, bucket);
  }
  const depths = Array.from(byDepth.keys()).sort((a, b) => a - b);

  return (
    <div className="space-y-3 max-h-[60vh] overflow-auto pr-1">
      {depths.map((d) => (
        <div key={d}>
          <div className="text-xs uppercase tracking-wider text-slate-500 mb-1">
            depth {d} ({byDepth.get(d)!.length})
          </div>
          <ul className="space-y-1">
            {byDepth.get(d)!.map((n) => (
              <li
                key={n.id}
                className="bg-slate-800/50 border border-slate-700 rounded px-2 py-1.5 text-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-slate-100 truncate">{n.name || n.id}</div>
                    <div className="text-xs text-slate-500 truncate">{n.id}</div>
                  </div>
                  <span className="pill shrink-0">{n.kind}</span>
                </div>
                {n.via_rels.length > 0 && (
                  <div className="mt-1 text-xs text-slate-400">
                    via{" "}
                    {n.via_rels.map((r, i) => (
                      <span
                        key={r + i}
                        className="pill mr-1 text-[10px] !px-1.5 !py-0"
                      >
                        {r}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
