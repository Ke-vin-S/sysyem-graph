"""QueryResolver: emit Query records for SQL/ORM call sites.

Driven by per-framework `queries` patterns. The resolver itself doesn't
know SQLAlchemy, JPA, or raw cursors — each framework YAML declares
which call shapes mean "a query is happening here" and which positional
arg holds the expression.

We walk CALL facts and match against the merged patterns. Each match
becomes a Query record, attributed to the function that contains it via
line-range matching (same enclosing-artifact logic as
FunctionCallResolver).

v1 extracts:
  * the literal SQL/expression string when arg is a string literal
  * a best-effort table list via regex on FROM/JOIN/INSERT INTO/UPDATE
  * the enclosing artifact ID for the EXECUTES edge

Out of scope: full SQL parsing (no sqlparse dependency), parameterized
queries reassembled across multiple lines, ORM call chains
(`session.query(User).filter(...)` — the resolver records the query but
table extraction yields the model name, not the SQL table).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from core.facts import FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import QueryPatterns
from core.types import CodeArtifact, Query, QueryKind


@dataclass
class QueryResolution:
    queries: list[Query]


_SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)",
    re.IGNORECASE,
)


class QueryResolver:
    def resolve(
        self,
        *,
        tree: FactTree,
        artifacts: Iterable[CodeArtifact],
        frameworks: tuple[EffectiveFramework, ...],
        repo_id: str,
        repo_root: str | None = None,
    ) -> QueryResolution:
        patterns = [fw.queries for fw in frameworks if fw.queries is not None]
        if not patterns:
            return QueryResolution(queries=[])

        # Same enclosing-artifact index pattern used by FunctionCallResolver:
        # (file, [(start, end, artifact)]) → smallest span containing the line.
        # Artifacts already carry repo-relative file paths; call facts hold
        # absolute paths from the walker. We rebase the latter to match.
        by_file_ranges: dict[str, list[tuple[int, int, CodeArtifact]]] = {}
        for art in artifacts:
            by_file_ranges.setdefault(art.file, []).append(
                (art.line_range.start, art.line_range.end, art)
            )

        seen_ids: set[str] = set()
        queries: list[Query] = []

        # Pass 1: CALL facts (Python `session.execute(...)`, raw cursor.execute).
        for call in tree.where(kind=FactKind.CALL):
            callee = str(call.data.get("callee", ""))
            method = str(call.data.get("method", ""))
            matched_pattern = _match_call(callee, method, patterns)
            if matched_pattern is None:
                continue
            query = _build_query_from_fact(
                fact=call, pattern=matched_pattern, repo_id=repo_id,
                repo_root=repo_root, by_file_ranges=by_file_ranges,
                id_prefix="q",
            )
            if query is None or query.id in seen_ids:
                continue
            seen_ids.add(query.id)
            queries.append(query)

        # Pass 2: ANNOTATION facts (Java `@Query("...")`). The annotation's
        # `target_symbol` is the method it wraps; we attribute the Query to
        # that method's artifact when known.
        for ann in tree.where(kind=FactKind.ANNOTATION):
            callee = str(ann.data.get("callee", ""))
            matched_pattern = _match_annotation(callee, patterns)
            if matched_pattern is None:
                continue
            query = _build_query_from_fact(
                fact=ann, pattern=matched_pattern, repo_id=repo_id,
                repo_root=repo_root, by_file_ranges=by_file_ranges,
                id_prefix="q",
            )
            if query is None or query.id in seen_ids:
                continue
            seen_ids.add(query.id)
            queries.append(query)

        return QueryResolution(queries=queries)


def _match_call(
    callee: str, method: str, patterns: list[QueryPatterns]
) -> QueryPatterns | None:
    for p in patterns:
        if p.call_callees and callee in p.call_callees:
            return p
        if p.call_methods and method in p.call_methods:
            return p
    return None


def _match_annotation(callee: str, patterns: list[QueryPatterns]) -> QueryPatterns | None:
    for p in patterns:
        if not p.annotation_callees:
            continue
        if callee in p.annotation_callees:
            return p
        if callee.rsplit(".", 1)[-1] in set(p.annotation_callees):
            return p
    return None


def _build_query_from_fact(
    *,
    fact,
    pattern: QueryPatterns,
    repo_id: str,
    repo_root: str | None,
    by_file_ranges: dict[str, list[tuple[int, int, CodeArtifact]]],
    id_prefix: str,
) -> Query | None:
    args = fact.data.get("args") or []
    arg_idx = pattern.expression_arg
    if arg_idx >= len(args):
        return None
    expr = args[arg_idx]
    if not isinstance(expr, str):
        return None
    if expr.startswith("<") and expr.endswith(">"):
        return None

    file = _rel_to(fact.file, repo_root) if repo_root else fact.file
    enclosing = _enclosing_artifact(by_file_ranges, file, fact.line)
    tables = tuple(_extract_tables(expr, pattern.kind))
    return Query(
        id=f"{id_prefix}:{repo_id}:{file}:{fact.line}",
        repoId=repo_id,
        kind=_normalize_kind(pattern.kind),
        file=file,
        line=fact.line,
        expression=expr,
        tables=tables,
        enclosingArtifactId=enclosing.id if enclosing else None,
    )


def _extract_tables(expression: str, kind: str) -> list[str]:
    """For raw_sql: regex FROM/JOIN/INTO/UPDATE. For other kinds, return []."""
    if kind != "raw_sql":
        return []
    matches = _SQL_TABLE_RE.findall(expression or "")
    seen: set[str] = set()
    out: list[str] = []
    for m in matches:
        name = m.split(".")[-1].strip("\"'`;").lower()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _enclosing_artifact(
    by_file_ranges: dict[str, list[tuple[int, int, CodeArtifact]]],
    file: str,
    line: int,
) -> CodeArtifact | None:
    ranges = by_file_ranges.get(file, ())
    best: CodeArtifact | None = None
    best_span = float("inf")
    for start, end, art in ranges:
        if start <= line <= end:
            span = end - start
            if span < best_span:
                best_span = span
                best = art
    return best


def _normalize_kind(kind: str) -> QueryKind:
    try:
        return QueryKind(kind)
    except ValueError:
        return QueryKind.RAW_SQL


def _rel_to(file: str, root: str) -> str:
    if not root:
        return file
    fp = PurePosixPath(file.replace("\\", "/"))
    rp = PurePosixPath(root.replace("\\", "/"))
    try:
        return str(fp.relative_to(rp))
    except ValueError:
        parts = fp.parts
        root_name = rp.name
        if root_name in parts:
            idx = parts.index(root_name)
            return str(PurePosixPath(*parts[idx + 1 :]))
        return file
