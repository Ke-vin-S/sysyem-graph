# system-graph UI

React + Vite frontend for the system-graph product. Pairs with the
FastAPI service in `../api/`.

## Layout

```
ui/
  src/
    api/          fetch wrapper + react-query hooks + Pydantic-mirror types
    components/   Layout, SearchBox, NodeDetail, ImpactPanel, GraphCanvas (Cytoscape)
    pages/        Explorer (the main view), Pipelines (read-only run state)
    App.tsx       router
    main.tsx      entry, QueryClient setup
```

## Dev

```bash
npm install
npm run dev        # http://localhost:5173 — proxies /api → :8000
```

The Vite dev server proxies `/api` and `/health` to the API on `:8000`,
so the two run side-by-side without CORS. For containerized dev, see
the top-level `docker compose --profile product` setup.

## Build

```bash
npm run build      # tsc --noEmit && vite build → ./dist
```

## Notes

* Graph visualisation: **Cytoscape.js** with `dagre` (hierarchical) and
  `cose-bilkent` (force-directed) layouts. Picked for its longevity in
  dependency-graph tooling and its layout selection — see
  [Cytoscape docs](https://js.cytoscape.org/).
* State: TanStack Query for all server data. No global store; the
  Explorer holds the small bits of UI state (selected node, layout
  choice, impact direction) locally.
* Types: hand-rolled `src/api/types.ts` mirrors the Pydantic schemas in
  `api/schemas/`. Small enough that codegen isn't worth the complexity.
