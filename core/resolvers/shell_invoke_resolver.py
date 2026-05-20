"""Resolve shell-script command calls to in-repo artifacts.

For every `CALL` fact emitted by `ShGrammar`, decide whether the callee
maps to (a) another shell function, (b) a compiled binary (C / Pro*C
function artifact), (c) another `.sh` script, or (d) a SQL*Plus script.
Anything else (`cp`, `mkdir`, system commands) is dropped — these are
infrastructure noise from an impact-analysis perspective.

For matches, the calling shell function (or the top-level "script
artifact" if the call is outside any function) gets the target appended
to its `calls` list.

This resolver does NOT mutate or emit SQL_STATEMENT facts — the
`sqlplus … @file.sql` capture is already an SQL_STATEMENT emitted by the
grammar. We re-use those facts here to link the calling shell function
to the `target_proc` script path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from core.facts import FactKind, FactTree
from core.types import CodeArtifact, LineRange

logger = logging.getLogger(__name__)


@dataclass
class ShellInvokeResolution:
    artifacts: list[CodeArtifact]
    edges: list[tuple[str, str]] = field(default_factory=list)


def resolve_shell_invokes(
    *,
    trees: dict[str, FactTree],
    artifacts: list[CodeArtifact],
) -> ShellInvokeResolution:
    """Return artifacts with `calls` extended for every shell→binary /
    shell→shell / shell→.sql edge that could be resolved locally."""
    # Index: artifacts by (repo, basename(file)) and by (repo, name).
    by_basename: dict[tuple[str, str], str] = {}
    by_name: dict[tuple[str, str], str] = {}
    sh_scripts: dict[tuple[str, str], str] = {}
    for a in artifacts:
        if a.type in {"function", "script"}:
            base = PurePosixPath(a.file).name
            by_basename[(a.repo_id, base)] = a.id
            by_name[(a.repo_id, a.name)] = a.id
            if a.type == "script":
                sh_scripts[(a.repo_id, base)] = a.id

    artifacts_by_id: dict[str, CodeArtifact] = {a.id: a for a in artifacts}
    edges: list[tuple[str, str]] = []
    new_script_artifacts: dict[str, CodeArtifact] = {}

    for repo_id, tree in trees.items():
        # Promote every `.sh` file to a script artifact so the resolver
        # has a "top-level entry point" to attach edges to. We only do
        # this when no existing artifact already represents the script.
        for sym in tree.where(kind=FactKind.SYMBOL):
            if sym.data.get("sym_kind") != "function":
                continue
            if not sym.file.endswith((".sh", ".bash", ".ksh", ".zsh")):
                continue
            # Already covered by a script entry?
            base = PurePosixPath(sym.file).name
            if (repo_id, base) in sh_scripts:
                continue
            script_id = f"script:{repo_id}:{base}"
            if script_id in new_script_artifacts:
                continue
            new_script_artifacts[script_id] = CodeArtifact(
                id=script_id,
                repoId=repo_id,
                type="script",
                name=base,
                file=_repo_rel(sym.file),
                lineRange=LineRange(start=1, end=1),
                isPublic=True,
                producedBy="shell_invoke_resolver",
            )
            sh_scripts[(repo_id, base)] = script_id

        # Resolve CALL facts → edges from the enclosing shell function (or
        # script artifact) to the matched target.
        for fact in tree.where(kind=FactKind.CALL):
            if not fact.file.endswith((".sh", ".bash", ".ksh", ".zsh")):
                continue
            callee = str(fact.data.get("callee", ""))
            target = _resolve_callee(callee, repo_id, by_basename, by_name)
            if target is None:
                continue
            source = _enclosing_script(
                tree, fact, repo_id, by_name, sh_scripts, new_script_artifacts
            )
            if source is None or source == target:
                continue
            edges.append((source, target))

        # SQL_STATEMENT facts in shell scripts: link the enclosing function
        # to the .sql file (if we know it as an artifact) — otherwise skip.
        for fact in tree.where(kind=FactKind.SQL_STATEMENT):
            if not fact.file.endswith((".sh", ".bash", ".ksh", ".zsh")):
                continue
            path = str(fact.data.get("target_proc", ""))
            if not path:
                continue
            target = _resolve_sql_path(path, repo_id, by_basename, artifacts_by_id)
            if target is None:
                continue
            source = _enclosing_script(
                tree, fact, repo_id, by_name, sh_scripts, new_script_artifacts
            )
            if source is None or source == target:
                continue
            edges.append((source, target))

    # Merge edges into the artifact list (existing artifacts + freshly
    # minted script artifacts).
    all_artifacts = list(artifacts) + list(new_script_artifacts.values())
    return ShellInvokeResolution(
        artifacts=_apply_edges(all_artifacts, edges),
        edges=edges,
    )


# ---- helpers --------------------------------------------------------------


def _resolve_callee(
    callee: str,
    repo_id: str,
    by_basename: dict[tuple[str, str], str],
    by_name: dict[tuple[str, str], str],
) -> str | None:
    if not callee:
        return None
    # `./bin/charge_loader`, `${BIN}/charge_loader`, `/opt/bin/charge_loader`
    base = PurePosixPath(callee).name
    hit = by_basename.get((repo_id, base))
    if hit:
        return hit
    # Bare command name (no path): treat as a function name.
    if "/" not in callee:
        return by_name.get((repo_id, callee))
    return None


def _resolve_sql_path(
    path: str,
    repo_id: str,
    by_basename: dict[tuple[str, str], str],
    artifacts_by_id: dict[str, CodeArtifact],
) -> str | None:
    base = PurePosixPath(path).name
    hit = by_basename.get((repo_id, base))
    if hit:
        return hit
    # Otherwise check whether an artifact's file ends with this path.
    for aid, a in artifacts_by_id.items():
        if a.repo_id != repo_id:
            continue
        if a.file.endswith(path) or PurePosixPath(a.file).name == base:
            return aid
    return None


def _enclosing_script(
    tree: FactTree,
    fact,
    repo_id: str,
    by_name: dict[tuple[str, str], str],
    sh_scripts: dict[tuple[str, str], str],
    new_script_artifacts: dict[str, CodeArtifact],
) -> str | None:
    """Find the enclosing shell function for `fact`. If the call is at
    top level (no enclosing function), return the script artifact."""
    # Find an enclosing function by line range — pick the SYMBOL whose line
    # is the closest at-or-before fact.line.
    enclosing = _enclosing_function_by_line(tree, fact)
    if enclosing is not None:
        name = enclosing.data.get("name", "")
        hit = by_name.get((repo_id, name))
        if hit:
            return hit
    base = PurePosixPath(fact.file).name
    fallback = sh_scripts.get((repo_id, base))
    if fallback:
        return fallback
    synthetic_id = f"script:{repo_id}:{base}"
    return synthetic_id if synthetic_id in new_script_artifacts else None


def _enclosing_function_by_line(tree: FactTree, fact):
    """Find the SYMBOL with sym_kind=function in the same file whose line
    is closest at-or-before fact.line. (Bash functions don't have
    line_end facts emitted, so we approximate by 'highest line ≤ N'.)"""
    best = None
    for sym in tree.where(kind=FactKind.SYMBOL, file=fact.file):
        if sym.data.get("sym_kind") != "function":
            continue
        if sym.line > fact.line:
            continue
        if best is None or sym.line > best.line:
            best = sym
    return best


def _repo_rel(file: str) -> str:
    """Best-effort: return the basename if we can't compute a true
    repo-relative path (we don't know the repo root here)."""
    return PurePosixPath(file).name


def _apply_edges(
    artifacts: list[CodeArtifact], edges: list[tuple[str, str]]
) -> list[CodeArtifact]:
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
    return out
