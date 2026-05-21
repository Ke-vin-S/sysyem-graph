"""Tests for the markdown report renderer (pure-function, no Neo4j)."""

from __future__ import annotations

from api.schemas.graph import GraphNode, ImpactNode, ImpactResponse
from api.services.report_service import render_impact_report


def _root() -> GraphNode:
    return GraphNode(
        id="proc:billing:pkg_billing:charge",
        kind="CodeArtifact",
        type="procedure",
        name="charge",
        repo_id="billing",
    )


def test_empty_impact_produces_explanatory_section() -> None:
    impact = ImpactResponse(root=_root(), direction="downstream", depth=3, nodes=[], edges=[])
    out = render_impact_report(impact)
    assert "Impact: charge" in out.markdown
    assert "_No impacted nodes" in out.markdown
    assert out.node_count == 0


def test_grouping_by_kind_is_stable_and_sorted() -> None:
    """The renderer must sort by depth then by name within each kind so
    diffs of two adjacent runs of the report are small."""
    impact = ImpactResponse(
        root=_root(),
        direction="downstream",
        depth=3,
        nodes=[
            ImpactNode(
                id="table:billing:invoice", kind="CodeArtifact", type="table",
                name="invoice", repo_id="billing", depth=1, via_rels=["READS"],
            ),
            ImpactNode(
                id="proc:billing:pkg_audit:log", kind="CodeArtifact", type="procedure",
                name="log", repo_id="billing", depth=1, via_rels=["CALLS"],
            ),
            ImpactNode(
                id="table:billing:audit_log", kind="CodeArtifact", type="table",
                name="audit_log", repo_id="billing", depth=2, via_rels=["CALLS", "WRITES"],
            ),
        ],
        edges=[],
    )
    out = render_impact_report(impact, title="Charge impact")
    assert "Charge impact" in out.markdown
    # `audit_log` (depth 2) must come AFTER `invoice` (depth 1) within
    # the CodeArtifact section because we sort by depth first.
    invoice_idx = out.markdown.index("**invoice**")
    audit_idx = out.markdown.index("**audit_log**")
    assert invoice_idx < audit_idx
    assert out.node_count == 3
    assert "via: `READS`" in out.markdown
