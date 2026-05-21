import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Explorer from "./pages/Explorer";
import Pipelines from "./pages/Pipelines";

// Single-page app, three routes. Explorer is the default landing
// because it's the marquee feature — pipelines is read-only state and
// users will hit it less often than the graph.
export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/explore" replace />} />
        <Route path="/explore" element={<Explorer />} />
        <Route path="/explore/:nodeId" element={<Explorer />} />
        <Route path="/pipelines" element={<Pipelines />} />
        <Route path="*" element={<Navigate to="/explore" replace />} />
      </Route>
    </Routes>
  );
}
