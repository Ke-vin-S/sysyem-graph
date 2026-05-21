"""Graph-read service.

Wraps low-level Neo4j queries with the shape the React UI consumes
(`GraphNode` / `GraphEdge`). The layer exists so the routers can stay
thin and so the same logic is reachable from a future web UI or batch
report generator without re-implementing Cypher.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from api.schemas.graph import (
    GraphEdge,
    GraphNode,
    GraphSubgraph,
    ImpactNode,
    ImpactResponse,
    SearchHit,
)
from core.graph.client import Neo4jClient

logger = logging.getLogger(__name__)


# Edges considered when walking impact in the "downstream" direction. We
# do NOT walk INITIATES/TARGETS pairs as two separate hops because the
# ExternalConnection node in the middle inflates depths; the UI cares
# about *service-to-service* hops, so the impact walker treats them as a
# single hop via the variable-length pattern in the Cypher query.
_DOWNSTREAM_RELS = (
    "CALLS",
    "READS",
    "WRITES",
    "EXPOSES",
    "CONTAINS",
    "DEFINES",
    "COVERS",
    "EXECUTES",
    "PRODUCES",
    "CONSUMES",
    "HANDLED_BY",
    "INITIATES",
    "TARGETS",
)

# Upstream = inbound. Same rels, walked backwards.
_UPSTREAM_RELS = _DOWNSTREAM_RELS


# Mapping from the props bag Neo4j returns -> the flat GraphNode shape
# the UI wants. Keeping the keys explicit here makes it obvious which
# fields the UI is allowed to see (and prevents "produced_by" /
# "from_facts" pollution).
_NAME_FIELDS = ("name", "id")
_TYPE_FIELDS = ("type", "kind")


@dataclass(frozen=True)
class _RawNode:
    labels: list[str]
    props: dict[str, Any]


class GraphService:
    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    # ---- nodes & search -----------------------------------------------

    def get_node(self, node_id: str) -> GraphNode | None:
        rows = self._client.run(
            "MATCH (n {id: $id}) RETURN labels(n) AS labels, properties(n) AS props LIMIT 1",
            id=node_id,
        )
        if not rows:
            return None
        return _to_graph_node(_RawNode(labels=rows[0]["labels"], props=rows[0]["props"]))

    def search(self, query: str, *, limit: int = 25) -> list[SearchHit]:
        """Substring search over name and id. Case-insensitive. The
        single CONTAINS-toLower(...) pattern is fast enough for the
        impact-graph sizes we deal with (10k-100k nodes); if we outgrow
        it the right move is a full-text index, not a smarter query."""
        if not query.strip():
            return []
        rows = self._client.run(
            """
            MATCH (n)
            WHERE toLower(n.name) CONTAINS toLower($q)
               OR toLower(n.id)   CONTAINS toLower($q)
            RETURN labels(n) AS labels, properties(n) AS props
            LIMIT $limit
            """,
            q=query,
            limit=int(limit),
        )
        return [_to_search_hit(_RawNode(labels=r["labels"], props=r["props"])) for r in rows]

    def list_services(self) -> list[GraphNode]:
        rows = self._client.run(
            """
            MATCH (s:Service)
            RETURN labels(s) AS labels, properties(s) AS props
            ORDER BY s.id
            """,
        )
        return [_to_graph_node(_RawNode(labels=r["labels"], props=r["props"])) for r in rows]

    # ---- subgraph for the explorer -----------------------------------

    def service_overview(self) -> GraphSubgraph:
        """All Services + the connections between them. The default
        landing view: small, fast, useful out of the box.

        Cardinality: O(services + service-to-service connections), so
        even at a few hundred services this fits in a single render."""
        nodes: list[GraphNode] = self.list_services()
        rows = self._client.run(
            """
            MATCH (a:Service)-[:INITIATES]->(:ExternalConnection)-[:TARGETS]->(b:Service)
            WHERE a.id <> b.id
            RETURN DISTINCT a.id AS source, b.id AS target
            """,
        )
        edges = [GraphEdge(source=r["source"], target=r["target"], rel="CALLS") for r in rows]
        return GraphSubgraph(nodes=nodes, edges=edges)

    def neighborhood(self, node_id: str, *, depth: int = 1) -> GraphSubgraph:
        """The node + its `depth`-hop neighborhood in both directions.

        Used when the user clicks a search result to anchor exploration.
        Capped at depth 3 by the router to prevent fan-out blow-up."""
        depth = max(1, min(depth, 3))
        rows = self._client.run(
            f"""
            MATCH path = (root {{id: $id}})-[*0..{depth}]-(n)
            WITH collect(DISTINCT n) AS ns, collect(DISTINCT relationships(path)) AS rss
            UNWIND ns AS n
            WITH ns, rss, n
            RETURN
                collect(DISTINCT {{labels: labels(n), props: properties(n)}}) AS nodes,
                rss AS rel_paths
            """,
            id=node_id,
        )
        if not rows:
            return GraphSubgraph(nodes=[], edges=[])
        row = rows[0]
        nodes = [
            _to_graph_node(_RawNode(labels=n["labels"], props=n["props"]))
            for n in row["nodes"] or []
        ]
        edges: set[tuple[str, str, str]] = set()
        for path in row["rel_paths"] or []:
            for rel in path:
                # neo4j returns Relationship objects; the driver's data()
                # path turns them into dicts with start/end/type when they
                # came from properties(), but in a relationships(path) bag
                # they remain Relationship instances. Handle both.
                start, end, rel_type = _unpack_relationship(rel)
                if start and end and rel_type:
                    edges.add((start, end, rel_type))
        return GraphSubgraph(
            nodes=nodes,
            edges=[GraphEdge(source=s, target=t, rel=r) for s, t, r in edges],
        )

    # ---- impact analysis ---------------------------------------------

    def impact(
        self,
        node_id: str,
        *,
        direction: str = "downstream",
        depth: int = 3,
    ) -> ImpactResponse | None:
        depth = max(1, min(depth, 8))
        root = self.get_node(node_id)
        if root is None:
            return None
        rels_list = list(_DOWNSTREAM_RELS) if direction == "downstream" else list(_UPSTREAM_RELS)
        # Cypher rel-types are uppercase identifiers — they can't be
        # parameterised, only string-interpolated. We control the source
        # list above so injection is not an issue.
        rels_pattern = "|".join(rels_list)
        arrow = "-[r:%s*1..%d]->" if direction == "downstream" else "<-[r:%s*1..%d]-"
        cypher = (
            "MATCH path = (root {id: $id})" + (arrow % (rels_pattern, depth)) + "(n) "
            "WITH n, min(size(relationships(path))) AS depth, "
            "     collect(DISTINCT [r IN relationships(path) | type(r)]) AS rel_lists "
            "RETURN labels(n) AS labels, properties(n) AS props, depth, "
            "       reduce(acc=[], rl IN rel_lists | acc + rl) AS rels "
            "ORDER BY depth, n.name"
        )
        rows = self._client.run(cypher, id=node_id)
        nodes: list[ImpactNode] = []
        seen_ids: set[str] = set()
        for row in rows:
            base = _to_graph_node(_RawNode(labels=row["labels"], props=row["props"]))
            if base.id == node_id:
                continue
            via = sorted({str(rel) for rel in row["rels"] or []})
            nodes.append(
                ImpactNode(
                    id=base.id,
                    kind=base.kind,
                    type=base.type,
                    name=base.name,
                    repo_id=base.repo_id,
                    file=base.file,
                    depth=int(row["depth"]),
                    via_rels=via,
                )
            )
            seen_ids.add(base.id)
        # Edges: just the relationships among the (root + reachable)
        # node set, so the UI can render an actual graph instead of a
        # star. Filter to keep the view interpretable.
        if seen_ids:
            ids_for_edge_match = list(seen_ids) + [node_id]
            edge_rows = self._client.run(
                "MATCH (a)-[r]->(b) "
                "WHERE a.id IN $ids AND b.id IN $ids "
                "RETURN a.id AS source, b.id AS target, type(r) AS rel",
                ids=ids_for_edge_match,
            )
            edges = [
                GraphEdge(source=r["source"], target=r["target"], rel=r["rel"])
                for r in edge_rows
            ]
        else:
            edges = []
        return ImpactResponse(
            root=root,
            direction=direction,
            depth=depth,
            nodes=nodes,
            edges=edges,
        )


# ---- helpers ----------------------------------------------------------


def _to_graph_node(raw: _RawNode) -> GraphNode:
    p = raw.props
    name = ""
    for key in _NAME_FIELDS:
        value = p.get(key)
        if value:
            name = str(value)
            break
    if not name:
        name = "(unnamed)"
    return GraphNode(
        id=str(p.get("id", "")),
        kind=raw.labels[0] if raw.labels else "Node",
        type=str(p.get("type", "")),
        name=name,
        repo_id=str(p.get("repo_id", "")),
        file=str(p.get("file", "")),
        language=str(p.get("language", "")),
        framework=str(p.get("framework", "")),
    )


def _to_search_hit(raw: _RawNode) -> SearchHit:
    p = raw.props
    return SearchHit(
        id=str(p.get("id", "")),
        kind=raw.labels[0] if raw.labels else "Node",
        name=str(p.get("name", p.get("id", ""))),
        repo_id=str(p.get("repo_id", "")),
        file=str(p.get("file", "")),
    )


def _unpack_relationship(rel: Any) -> tuple[str, str, str]:
    """Pull (start_node_id, end_node_id, rel_type) out of a Neo4j
    Relationship in a way that works whether the driver hands us a typed
    object or a plain dict."""
    if hasattr(rel, "start_node") and hasattr(rel, "end_node"):
        start = rel.start_node.get("id", "")  # type: ignore[attr-defined]
        end = rel.end_node.get("id", "")  # type: ignore[attr-defined]
        rel_type = rel.type  # type: ignore[attr-defined]
        return str(start), str(end), str(rel_type)
    if isinstance(rel, dict):
        return (
            str(rel.get("start", rel.get("source", ""))),
            str(rel.get("end", rel.get("target", ""))),
            str(rel.get("type", rel.get("rel", ""))),
        )
    return "", "", ""
