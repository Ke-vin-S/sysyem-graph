/// <reference types="vite/client" />

// Untyped Cytoscape plugins — declare them so the compiler accepts the
// imports. The plugins are tiny `cytoscape.use(...)`-style registrars
// that don't expose a TypeScript surface of their own.
declare module "cytoscape-dagre" {
  const plugin: cytoscape.Ext;
  export default plugin;
}

declare module "cytoscape-cose-bilkent" {
  const plugin: cytoscape.Ext;
  export default plugin;
}
