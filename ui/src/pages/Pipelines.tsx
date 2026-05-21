import { usePipelines } from "../api/hooks";
import type { PipelineSummary } from "../api/types";

// Read-only dashboard. One card per adapter showing what we know from
// its SQLite store plus the env config it's running with. The CLI
// remains the source of truth for actually running ingestion.
export default function Pipelines() {
  const { data, isLoading, error } = usePipelines();

  if (isLoading) {
    return <div className="p-6 text-slate-400">Loading pipelines…</div>;
  }
  if (error) {
    return (
      <div className="p-6 text-rose-400">
        Couldn't load pipelines. Is the API up? <code>uvicorn api.main:app</code>
      </div>
    );
  }
  const pipelines = data?.pipelines ?? [];

  return (
    <div className="p-6 space-y-4 max-w-5xl mx-auto">
      <div>
        <h1 className="text-xl font-semibold">Pipelines</h1>
        <p className="text-sm text-slate-400">
          Read-only view of adapter state. To trigger a run, use{" "}
          <code className="bg-slate-800 px-1 rounded">sg-ingest</code> in the
          terminal.
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {pipelines.map((p) => (
          <PipelineCard key={p.id} pipeline={p} />
        ))}
      </div>
    </div>
  );
}

function statusPill(status: string): string {
  switch (status) {
    case "ok":
      return "pill pill-ok";
    case "stale":
      return "pill pill-stale";
    case "error":
      return "pill pill-err";
    case "disabled":
      return "pill pill-off";
    default:
      return "pill";
  }
}

function PipelineCard({ pipeline }: { pipeline: PipelineSummary }) {
  return (
    <div className="card">
      <div className="flex items-start justify-between mb-2">
        <div>
          <h2 className="text-lg font-semibold">{pipeline.label}</h2>
          <div className="text-xs text-slate-400">{pipeline.id}</div>
        </div>
        <span className={statusPill(pipeline.status)}>{pipeline.status}</span>
      </div>
      <div className="text-sm text-slate-300 mb-3">{pipeline.detail}</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        {Object.entries(pipeline.config).map(([k, v]) => (
          <div key={k} className="contents">
            <div className="text-slate-400 break-words">{k}</div>
            <div className="text-slate-200 break-words">{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
