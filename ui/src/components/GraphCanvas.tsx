import { useEffect, useRef } from "react";
import cytoscape, { Core, EdgeDefinition, NodeDefinition } from "cytoscape";
import dagre from "cytoscape-dagre";
import coseBilkent from "cytoscape-cose-bilkent";
import type { GraphEdge, GraphNode } from "../api/types";

// Register Cytoscape extensions once at module load. Registering twice
// is a noop but logs a warning, so we cheaply guard against StrictMode
// double-effects in dev.
type WithExtensions = typeof cytoscape & { _systemGraphRegistered?: boolean };
const cy = cytoscape as WithExtensions;
if (!cy._systemGraphRegistered) {
  cytoscape.use(dagre);
  cytoscape.use(coseBilkent);
  cy._systemGraphRegistered = true;
}

export type GraphLayout = "dagre" | "cose" | "breadthfirst";

interface GraphCanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedId?: string | null;
  highlightedIds?: Set<string>;
  layout?: GraphLayout;
  onSelect?: (nodeId: string) => void;
}

// Maps node kinds to a stable color so users can tell Services from
// Tables from Procedures at a glance. Keep this in sync with the legend
// at the bottom of Explorer.tsx.
const KIND_COLORS: Record<string, string> = {
  Service: "#6ea8ff",
  CodeArtifact: "#7ee787",
  TestCase: "#f0883e",
  ExternalConnection: "#bc8cff",
  Endpoint: "#79c0ff",
  KafkaTopic: "#ffa657",
  KafkaProducer: "#ffa657",
  KafkaConsumer: "#ffa657",
  Query: "#d2a8ff",
  DataModel: "#a5d6ff",
  Mock: "#ffa198",
  Node: "#888888",
};

// Shape per kind reinforces the color cue for colorblind users.
const KIND_SHAPES: Record<string, string> = {
  Service: "round-rectangle",
  CodeArtifact: "ellipse",
  TestCase: "diamond",
  ExternalConnection: "hexagon",
  Endpoint: "tag",
  KafkaTopic: "barrel",
  KafkaProducer: "vee",
  KafkaConsumer: "vee",
};

export default function GraphCanvas({
  nodes,
  edges,
  selectedId,
  highlightedIds,
  layout = "dagre",
  onSelect,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);

  // One Cytoscape instance lives for the component's lifetime. We
  // diff nodes/edges on subsequent renders rather than tearing down
  // and rebuilding, so layout state and zoom survive prop changes.
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      wheelSensitivity: 0.2,
      minZoom: 0.2,
      maxZoom: 2.5,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            shape: "data(shape)" as never,
            label: "data(label)",
            color: "#cbd5e1",
            "font-size": 11,
            "text-valign": "bottom",
            "text-margin-y": 4,
            "text-outline-width": 2,
            "text-outline-color": "#0b1220",
            "border-width": 1,
            "border-color": "#1f2937",
            width: 24,
            height: 24,
          },
        },
        {
          selector: "node.selected",
          style: {
            "border-color": "#facc15",
            "border-width": 3,
            "background-color": "#facc15",
            width: 36,
            height: 36,
          },
        },
        {
          selector: "node.highlighted",
          style: {
            "border-color": "#f97316",
            "border-width": 2,
          },
        },
        {
          selector: "node.dimmed",
          style: {
            opacity: 0.25,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1,
            "line-color": "#475569",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#475569",
            "curve-style": "bezier",
            opacity: "0.7" as never,
            label: "data(rel)",
            "font-size": 9,
            color: "#94a3b8",
            "text-rotation": "autorotate" as never,
            "text-background-color": "#0b1220",
            "text-background-opacity": "1" as never,
            "text-background-padding": "2" as never,
          },
        },
        {
          selector: "edge.highlighted",
          style: {
            width: 2,
            "line-color": "#f97316",
            "target-arrow-color": "#f97316",
            opacity: "1" as never,
          },
        },
        {
          selector: "edge.dimmed",
          style: {
            opacity: "0.1" as never,
          },
        },
      ],
    });
    cy.on("tap", "node", (event) => {
      const id = event.target.id();
      if (onSelect) onSelect(id);
    });
    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
    // We intentionally do not depend on onSelect — recreating the cy
    // instance on every parent render would lose layout/zoom state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync elements whenever the nodes/edges props change.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const cyNodes: NodeDefinition[] = nodes.map((n) => ({
      data: {
        id: n.id,
        label: n.name || n.id,
        kind: n.kind,
        type: n.type,
        color: KIND_COLORS[n.kind] ?? KIND_COLORS.Node,
        shape: KIND_SHAPES[n.kind] ?? "ellipse",
      },
    }));
    const cyEdges: EdgeDefinition[] = edges.map((e, i) => ({
      data: {
        id: `${e.source}->${e.target}::${e.rel}::${i}`,
        source: e.source,
        target: e.target,
        rel: e.rel,
      },
    }));
    cy.elements().remove();
    cy.add([...cyNodes, ...cyEdges]);
    cy.layout(layoutOptions(layout)).run();
  }, [nodes, edges, layout]);

  // Highlight / select markers — applied as CSS classes.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("selected highlighted dimmed");
      if (selectedId) {
        const sel = cy.getElementById(selectedId);
        if (sel.nonempty()) sel.addClass("selected");
      }
      if (highlightedIds && highlightedIds.size > 0) {
        const inSet = (id: string) => highlightedIds.has(id);
        cy.nodes().forEach((n) => {
          if (inSet(n.id())) n.addClass("highlighted");
          else if (n.id() !== selectedId) n.addClass("dimmed");
        });
        cy.edges().forEach((e) => {
          if (inSet(e.source().id()) && inSet(e.target().id())) {
            e.addClass("highlighted");
          } else {
            e.addClass("dimmed");
          }
        });
      }
    });
  }, [selectedId, highlightedIds]);

  return <div ref={containerRef} className="cy-canvas" />;
}

function layoutOptions(layout: GraphLayout) {
  switch (layout) {
    case "cose":
      return {
        name: "cose-bilkent",
        animate: false,
        nodeRepulsion: 9000,
        idealEdgeLength: 90,
        edgeElasticity: 0.45,
        nodeDimensionsIncludeLabels: true,
      } as never;
    case "breadthfirst":
      return {
        name: "breadthfirst",
        directed: true,
        padding: 30,
        spacingFactor: 1.4,
      } as never;
    case "dagre":
    default:
      return {
        name: "dagre",
        rankDir: "LR",
        nodeSep: 40,
        rankSep: 80,
      } as never;
  }
}
