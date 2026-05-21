"""Pydantic schemas for graph endpoints.

These are the wire format the React UI consumes. They're intentionally a
flatter shape than `core/types` — the UI doesn't care about pydantic
aliases or provenance internals, and we don't want to leak them. If the
internal shape changes, only this file needs adjusting.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """One node in the graph view — flat enough for Cytoscape to consume.

    `kind` is the Neo4j label (Service / CodeArtifact / TestCase / Endpoint /
    DataModel / Query / KafkaTopic / KafkaProducer / KafkaConsumer / Mock /
    ExternalConnection). `type` is the artifact subtype (procedure / function /
    table / endpoint / …) when relevant, empty otherwise."""

    id: str
    kind: str
    type: str = ""
    name: str
    repo_id: str = ""
    file: str = ""
    language: str = ""
    framework: str = ""


class GraphEdge(BaseModel):
    source: str
    target: str
    rel: str


class GraphSubgraph(BaseModel):
    """Result of a graph-view query: nodes + edges. The UI hands this
    straight to Cytoscape after a tiny adapter pass."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class GraphCountsResponse(BaseModel):
    services: int
    artifacts: int
    tests: int
    connections: int
    edges: dict[str, int]


class SearchHit(BaseModel):
    id: str
    kind: str
    name: str
    repo_id: str = ""
    file: str = ""


class ImpactNode(BaseModel):
    """A node reachable from the root with its distance, on a specific
    edge family (downstream = follows outgoing; upstream = follows
    incoming). The `via_rels` set lets the UI render edge type chips."""

    id: str
    kind: str
    type: str = ""
    name: str
    repo_id: str = ""
    file: str = ""
    depth: int
    via_rels: list[str] = Field(default_factory=list)


class ImpactResponse(BaseModel):
    root: GraphNode
    direction: str
    """`downstream` (what this node calls / writes) or `upstream` (callers)."""
    depth: int
    nodes: list[ImpactNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
