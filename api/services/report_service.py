"""Markdown report generation.

Reports are derived from an impact-analysis result. The renderer is plain
string concatenation — no template engine — because the output is small
and the format is easy to read in raw form (which it has to be, since the
markdown is the user-facing deliverable, not an intermediate)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from api.schemas.graph import ImpactResponse
from api.schemas.reports import ImpactReportResponse


def render_impact_report(
    impact: ImpactResponse,
    *,
    title: str = "",
) -> ImpactReportResponse:
    """Group impacted nodes by kind and produce a readable bulleted list.

    The first section is a short header so the report makes sense out of
    context (e.g. when pasted into a PR comment); subsequent sections are
    keyed by node kind so reviewers can scan by category."""
    root = impact.root
    direction_label = (
        "Downstream impact (this artifact's dependencies)"
        if impact.direction == "downstream"
        else "Upstream impact (callers of this artifact)"
    )
    resolved_title = title or f"Impact: {root.name or root.id}"
    generated_at = datetime.now(timezone.utc).isoformat()

    by_kind: dict[str, list] = defaultdict(list)
    for node in impact.nodes:
        by_kind[node.kind].append(node)

    lines: list[str] = []
    lines.append(f"# {resolved_title}")
    lines.append("")
    lines.append(f"- **Root**: `{root.id}` ({root.kind}{f', {root.type}' if root.type else ''})")
    lines.append(f"- **Direction**: {direction_label}")
    lines.append(f"- **Depth**: up to {impact.depth} hops")
    lines.append(f"- **Affected nodes**: {len(impact.nodes)}")
    lines.append(f"- **Generated**: {generated_at}")
    lines.append("")

    if not impact.nodes:
        lines.append("_No impacted nodes within the configured depth._")
        return ImpactReportResponse(
            title=resolved_title,
            markdown="\n".join(lines),
            generated_at=generated_at,
            node_count=0,
        )

    for kind in sorted(by_kind.keys()):
        nodes = sorted(by_kind[kind], key=lambda n: (n.depth, n.name.lower()))
        lines.append(f"## {kind} ({len(nodes)})")
        lines.append("")
        for node in nodes:
            extras: list[str] = [f"depth {node.depth}"]
            if node.repo_id:
                extras.append(f"repo: `{node.repo_id}`")
            if node.type:
                extras.append(f"type: `{node.type}`")
            if node.via_rels:
                extras.append(f"via: `{', '.join(node.via_rels)}`")
            lines.append(f"- `{node.id}` — **{node.name}** ({'; '.join(extras)})")
        lines.append("")

    return ImpactReportResponse(
        title=resolved_title,
        markdown="\n".join(lines),
        generated_at=generated_at,
        node_count=len(impact.nodes),
    )


__all__ = ["render_impact_report"]
