"""Resolve table/view reads and writes from SQL_STATEMENT facts.

For every `SQL_STATEMENT` fact:
  * `operation='select'`  → enclosing artifact READS the listed tables.
  * `operation in {'insert','update','delete','merge','truncate'}` →
    enclosing artifact WRITES them.
  * `operation='call'|'execute'|'script'` is a code-call, not a data
    access — handled by `OracleCallResolver`, ignored here.

Emits:
  * One `CodeArtifact(type='table')` per unique table touched, with id
    `table:<repo_id>:<name>` (schema preserved verbatim: `app.customer`).
    The same table touched across repos produces one CodeArtifact per
    repo — same physical table, different graph nodes, by design (so an
    impact query scoped to one repo stays scoped).
  * Updated artifact records with `reads` / `writes` extended.

Resolution of the enclosing artifact uses `tree.symbol_at(file, line)`:
the deepest function/procedure whose line range covers the SQL site.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from core.facts import FactKind, FactTree
from core.types import CodeArtifact, LineRange

logger = logging.getLogger(__name__)


_READ_OPS = frozenset({"select"})
_WRITE_OPS = frozenset({"insert", "update", "delete", "merge", "truncate"})


@dataclass
class DbAccessResolution:
    artifacts: list[CodeArtifact]
    table_artifacts: list[CodeArtifact] = field(default_factory=list)


def resolve_db_access(
    *,
    trees: dict[str, FactTree],
    artifacts: list[CodeArtifact],
) -> DbAccessResolution:
    """Return artifacts with `reads`/`writes` populated and a fresh list
    of table CodeArtifacts."""
    artifacts_by_id: dict[str, CodeArtifact] = {a.id: a for a in artifacts}
    reads: dict[str, set[str]] = {a.id: set(a.reads) for a in artifacts}
    writes: dict[str, set[str]] = {a.id: set(a.writes) for a in artifacts}
    table_artifacts: dict[str, CodeArtifact] = {}

    for repo_id, tree in trees.items():
        for fact in tree.where(kind=FactKind.SQL_STATEMENT):
            op = fact.data.get("operation", "")
            tables = fact.data.get("tables") or []
            if not tables:
                continue
            if op not in _READ_OPS and op not in _WRITE_OPS:
                continue
            source_id = _enclosing_artifact_id(tree, fact, artifacts_by_id)
            if source_id is None:
                continue
            target_set = reads if op in _READ_OPS else writes
            for tbl in tables:
                tbl_norm = tbl.strip().lower()
                if not tbl_norm:
                    continue
                tbl_id = f"table:{repo_id}:{tbl_norm}"
                if tbl_id not in table_artifacts:
                    table_artifacts[tbl_id] = CodeArtifact(
                        id=tbl_id,
                        repoId=repo_id,
                        type="table",
                        name=tbl_norm,
                        file=f"sql:{tbl_norm}",
                        lineRange=LineRange(start=1, end=1),
                        isPublic=True,
                        producedBy="db_access_resolver",
                    )
                target_set[source_id].add(tbl_id)

    out_artifacts: list[CodeArtifact] = []
    for a in artifacts:
        new_reads = tuple(sorted(reads[a.id]))
        new_writes = tuple(sorted(writes[a.id]))
        if new_reads == a.reads and new_writes == a.writes:
            out_artifacts.append(a)
        else:
            out_artifacts.append(
                a.model_copy(update={"reads": new_reads, "writes": new_writes})
            )

    return DbAccessResolution(
        artifacts=out_artifacts + list(table_artifacts.values()),
        table_artifacts=list(table_artifacts.values()),
    )


def _enclosing_artifact_id(
    tree: FactTree, fact, artifacts_by_id: dict[str, CodeArtifact]
) -> str | None:
    """The innermost artifact whose line range covers this fact."""
    del tree  # unused — kept in signature for future tree-based lookups
    return enclosing_artifact_id_for(
        fact_file=fact.file,
        fact_line=fact.line,
        fact_repo_id=fact.repo_id,
        artifacts_by_id=artifacts_by_id,
    )


def enclosing_artifact_id_for(
    *,
    fact_file: str,
    fact_line: int,
    fact_repo_id: str,
    artifacts_by_id: dict[str, CodeArtifact],
) -> str | None:
    """Return the artifact whose body covers `(fact_file, fact_line)`.

    For artifacts with a real `line_end` (C functions, Python defs),
    "covers" means the line falls inside the declared range. For
    artifacts with `line_end == line_start` (PL/SQL grammars don't emit
    end lines), we fall back to "the most recent declaration at or
    before fact_line" — which is correct for non-nested languages like
    PL/SQL and shell.

    File matching is by exact path, suffix, or basename, since adapters
    use different path conventions at different stages.
    """
    fact_basename = PurePosixPath(fact_file).name
    in_range_best_id: str | None = None
    in_range_best_span: float = float("inf")
    proximity_best_id: str | None = None
    proximity_best_line: int = -1

    for aid, a in artifacts_by_id.items():
        if a.repo_id != fact_repo_id:
            continue
        if a.type not in {"procedure", "function", "trigger", "method"}:
            continue
        if not _file_matches(a.file, fact_file, fact_basename):
            continue
        span = a.line_range.end - a.line_range.start
        if span > 0 and a.line_range.start <= fact_line <= a.line_range.end:
            if span < in_range_best_span:
                in_range_best_span = span
                in_range_best_id = aid
        elif a.line_range.start <= fact_line and a.line_range.start > proximity_best_line:
            # Fallback: most-recent-decl-before-fact for grammars that
            # don't emit an accurate `line_end`.
            proximity_best_line = a.line_range.start
            proximity_best_id = aid

    return in_range_best_id or proximity_best_id


def _file_matches(art_file: str, fact_file: str, fact_basename: str) -> bool:
    if art_file == fact_file:
        return True
    if fact_file.endswith(art_file) or art_file.endswith(fact_file):
        return True
    return PurePosixPath(art_file).name == fact_basename
