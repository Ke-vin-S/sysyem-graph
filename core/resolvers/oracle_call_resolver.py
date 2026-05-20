"""Resolve PL/SQL cross-procedure calls and Pro*C/C → PL/SQL invocations.

Two fact sources:

1. **`CALL` facts in PL/SQL files.** A `pkg_a.proc_x(...)` call inside a
   procedure body. We resolve the callee to the `CodeArtifact.id` of the
   procedure being called, when that procedure exists in the same set of
   artifacts.

2. **`SQL_STATEMENT` facts with `operation='call'|'execute'` and a
   `target_proc` populated.** These come from Pro*C `EXEC SQL CALL` /
   `EXEC SQL EXECUTE BEGIN pkg.proc(...)` blocks. The target_proc string
   names the procedure being invoked.

Output:
  * Updated `CodeArtifact` records with `calls` extended to include the
    resolved targets.
  * Newly-emitted `CodeArtifact(type='procedure')` records for any
    target referenced by a call/sql but not declared in the input set
    (so unresolved cross-repo calls still produce nodes the graph can
    point to — flagged with `is_public=True` and a stable id).

The resolver is repo-aware: it indexes artifacts by `(repo_id, name)` and
by `(repo_id, package, name)`. A caller in repo `billing` invoking
`pkg_audit.log` first looks in `billing`, then in any other repo that
exports a matching procedure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.facts import FactKind, FactTree
from core.types import CodeArtifact, LineRange

logger = logging.getLogger(__name__)


@dataclass
class OracleCallResolution:
    """Result of resolving Oracle-style calls across a set of repos."""

    artifacts: list[CodeArtifact]
    """Input artifacts with `calls` extended."""
    edges: list[tuple[str, str]] = field(default_factory=list)
    """(source_id, target_id) for diagnostic / test introspection."""


def resolve_oracle_calls(
    *,
    trees: dict[str, FactTree],
    artifacts: list[CodeArtifact],
) -> OracleCallResolution:
    """Resolve PL/SQL and Pro*C/EXEC SQL calls.

    `trees` maps `repo_id -> FactTree`. `artifacts` is the union of all
    code artifacts across those repos (typically PL/SQL procedures /
    functions / C functions). Returns a new artifact list (input is not
    mutated) with `calls` extended.
    """
    # Index: (repo_id, name) and (repo_id, package, name) → artifact id.
    by_name: dict[tuple[str, str], str] = {}
    by_pkg: dict[tuple[str, str, str], str] = {}
    for a in artifacts:
        if a.type not in {"procedure", "function", "trigger"}:
            continue
        name = a.name.lower()
        by_name[(a.repo_id, name)] = a.id
        # If the artifact is in a package, also key by (repo, pkg, name).
        # We encode package in the artifact id format: `proc:<repo>:<pkg>:<name>`
        # — see `_proc_id` below. Recover the pkg from there.
        pkg = _pkg_from_id(a.id)
        if pkg:
            by_pkg[(a.repo_id, pkg, name)] = a.id

    artifacts_by_id: dict[str, CodeArtifact] = {a.id: a for a in artifacts}
    extra_targets: dict[str, CodeArtifact] = {}
    edges: list[tuple[str, str]] = []

    # For attaching CALL targets to the enclosing procedure: find the
    # procedure containing the call site by `tree.symbol_at(file, line)`.
    for repo_id, tree in trees.items():
        for fact in tree.where(kind=FactKind.CALL):
            target = _resolve_call(
                fact.data.get("callee", ""), repo_id, by_name, by_pkg,
            )
            if target is None:
                continue
            source_id = _enclosing_proc_id(tree, fact, artifacts_by_id)
            if source_id is None:
                continue
            edges.append((source_id, target))

        for fact in tree.where(kind=FactKind.SQL_STATEMENT):
            # Only `CALL` / `EXECUTE PROCEDURE` forms represent procedure
            # invocations. `operation='script'` (sqlplus @file.sql) is
            # handled by ShellInvokeResolver as a script-to-script edge —
            # treating it as a PL/SQL call would misparse paths like
            # `sql/cleanup.sql` as `<pkg>.<name>`.
            if fact.data.get("operation") not in ("call", "execute"):
                continue
            target_proc = fact.data.get("target_proc", "")
            if not target_proc:
                continue
            target = _resolve_call(target_proc, repo_id, by_name, by_pkg)
            if target is None:
                # Surface unresolved cross-repo call targets as new
                # procedure nodes so the graph can at least point at them.
                target = _materialize_unknown_procedure(
                    repo_id=repo_id,
                    qualified=target_proc,
                    by_pkg=by_pkg,
                    extras=extra_targets,
                )
            source_id = _enclosing_proc_id(tree, fact, artifacts_by_id)
            if source_id is None:
                # Pro*C / C — the enclosing symbol is a C function.
                source_id = _enclosing_c_function(tree, fact, artifacts_by_id)
            if source_id is None:
                continue
            edges.append((source_id, target))

    return OracleCallResolution(
        artifacts=_apply_call_edges(artifacts, edges) + list(extra_targets.values()),
        edges=edges,
    )


# ---- helpers --------------------------------------------------------------


def _resolve_call(
    callee: str,
    repo_id: str,
    by_name: dict[tuple[str, str], str],
    by_pkg: dict[tuple[str, str, str], str],
) -> str | None:
    if not callee:
        return None
    parts = callee.lower().split(".")
    if len(parts) >= 2:
        pkg = parts[-2]
        name = parts[-1]
        # Repo-local match first
        hit = by_pkg.get((repo_id, pkg, name))
        if hit:
            return hit
        # Cross-repo match — any repo with that (pkg, name) pair.
        for (_r, p, n), aid in by_pkg.items():
            if p == pkg and n == name:
                return aid
        return None
    # Unqualified call — only resolve if there's a unique match locally.
    name = parts[0]
    return by_name.get((repo_id, name))


def _materialize_unknown_procedure(
    *,
    repo_id: str,
    qualified: str,
    by_pkg: dict[tuple[str, str, str], str],
    extras: dict[str, CodeArtifact],
) -> str:
    """Emit a stub `CodeArtifact(type='procedure')` for an unresolved
    target. Stable id so re-runs don't duplicate."""
    parts = qualified.lower().split(".")
    pkg = parts[-2] if len(parts) >= 2 else ""
    name = parts[-1]
    aid = _proc_id(repo_id="external", pkg=pkg, name=name)
    if aid in extras:
        return aid
    extras[aid] = CodeArtifact(
        id=aid,
        repoId="external",
        type="procedure",
        name=qualified.lower(),
        file=f"external:{qualified.lower()}",
        lineRange=LineRange(start=1, end=1),
        isPublic=True,
        producedBy="oracle_call_resolver",
    )
    # Index it so subsequent calls in this run resolve to the stub.
    by_pkg[("external", pkg, name)] = aid
    return aid


def _enclosing_proc_id(
    tree: FactTree, fact, artifacts_by_id: dict[str, CodeArtifact]
) -> str | None:
    """Find the procedure/function whose body covers this fact's line."""
    from core.resolvers.db_access_resolver import enclosing_artifact_id_for

    del tree  # we work off artifact line-ranges; tree retained for future use
    return enclosing_artifact_id_for(
        fact_file=fact.file,
        fact_line=fact.line,
        fact_repo_id=fact.repo_id,
        artifacts_by_id=artifacts_by_id,
    )


def _enclosing_c_function(
    tree: FactTree, fact, artifacts_by_id: dict[str, CodeArtifact]
) -> str | None:
    """For Pro*C: find the C function containing a SQL_STATEMENT fact."""
    from core.resolvers.db_access_resolver import enclosing_artifact_id_for

    del tree
    aid = enclosing_artifact_id_for(
        fact_file=fact.file,
        fact_line=fact.line,
        fact_repo_id=fact.repo_id,
        artifacts_by_id=artifacts_by_id,
    )
    if aid is None:
        return None
    a = artifacts_by_id.get(aid)
    return aid if a is not None and a.type == "function" else None


def _apply_call_edges(
    artifacts: list[CodeArtifact], edges: list[tuple[str, str]]
) -> list[CodeArtifact]:
    """Return new artifacts with `calls` extended per the edge list.
    Dedupes; preserves existing `calls` entries."""
    by_id = {a.id: a for a in artifacts}
    extended: dict[str, set[str]] = {a.id: set(a.calls) for a in artifacts}
    for src, dst in edges:
        if src in extended:
            extended[src].add(dst)
    out: list[CodeArtifact] = []
    for a in artifacts:
        new_calls = tuple(sorted(extended[a.id]))
        if new_calls == a.calls:
            out.append(a)
        else:
            out.append(a.model_copy(update={"calls": new_calls}))
    del by_id  # unused; kept for clarity
    return out


# ---- id helpers -----------------------------------------------------------

def _proc_id(*, repo_id: str, pkg: str, name: str) -> str:
    if pkg:
        return f"proc:{repo_id}:{pkg}:{name.lower()}"
    return f"proc:{repo_id}::{name.lower()}"


def _pkg_from_id(artifact_id: str) -> str:
    if not artifact_id.startswith("proc:"):
        return ""
    parts = artifact_id.split(":")
    # proc:repo:pkg:name → parts = ['proc', repo, pkg, name]; pkg may be empty
    if len(parts) >= 4:
        return parts[2]
    return ""
