import { NavLink, Outlet } from "react-router-dom";
import { useGraphCounts, useNeo4jHealth } from "../api/hooks";

// App chrome: top bar with nav + Neo4j connection state, then the page
// outlet. The explorer fills the viewport from underneath, so we keep
// the chrome thin.
export default function Layout() {
  const health = useNeo4jHealth();
  const counts = useGraphCounts();

  const connected = health.data?.reachable === true;
  const totalNodes =
    (counts.data?.services ?? 0) +
    (counts.data?.artifacts ?? 0) +
    (counts.data?.tests ?? 0) +
    (counts.data?.connections ?? 0);

  return (
    <div className="h-full flex flex-col bg-canvas">
      <header className="border-b border-slate-700 bg-slate-900/80 backdrop-blur px-4 py-2 flex items-center gap-4">
        <div className="font-semibold text-accent">system-graph</div>
        <nav className="flex gap-3 text-sm">
          <NavLink
            to="/explore"
            className={({ isActive }) =>
              `px-2 py-1 rounded ${
                isActive ? "bg-slate-700 text-white" : "text-slate-300 hover:text-white"
              }`
            }
          >
            Explorer
          </NavLink>
          <NavLink
            to="/pipelines"
            className={({ isActive }) =>
              `px-2 py-1 rounded ${
                isActive ? "bg-slate-700 text-white" : "text-slate-300 hover:text-white"
              }`
            }
          >
            Pipelines
          </NavLink>
        </nav>
        <div className="flex-1" />
        <div className="text-xs text-slate-400">
          {connected ? (
            <span className="pill pill-ok">
              neo4j: {health.data?.database} • {totalNodes.toLocaleString()} nodes
            </span>
          ) : (
            <span className="pill pill-err">
              neo4j unreachable{health.data?.uri ? ` @ ${health.data.uri}` : ""}
            </span>
          )}
        </div>
      </header>
      <main className="flex-1 min-h-0">
        <Outlet />
      </main>
    </div>
  );
}
