"""FunctionResolver: emit CodeArtifact records for the code structure map.

This is the "what's in this repo?" resolver. For every top-level public
function, class, and method, emit a CodeArtifact. Combined with
EndpointResolver, this gives Neo4j a complete map of the repository's
named code surface — the substrate the impact engine queries against.

Heuristics for "public":
  * Python: name doesn't start with `_`. Top-level only for `function`;
    `method` records include the enclosing class in their data so a
    consumer can scope queries.
  * Java: any method/class that's not an anonymous inner. We don't try
    to filter by `private` modifier here — Spring/JPA reflection makes
    visibility a poor proxy for "public surface".

Files under common test directories are excluded; tests are emitted by
TestResolver, not by this resolver.
"""

from __future__ import annotations

from pathlib import Path

from core.facts import Fact, FactKind, FactTree
from core.resolvers.resolver import ResolverContext
from core.types import CodeArtifact, LineRange


_EXCLUDED_PATH_PARTS = frozenset({"tests", "test"})


class FunctionResolver:
    """Build CodeArtifact records (type=function|class|method) from a FactTree.

    Output is intentionally conservative: one artifact per SYMBOL or CLASS_DEF
    fact, with `is_public` derived from name + modifiers. The downstream
    Neo4j loader can decide whether to load all of them or filter further.
    """

    def resolve(self, context: ResolverContext) -> list[CodeArtifact]:
        out: list[CodeArtifact] = []
        seen: set[str] = set()
        for fact in context.tree:
            if fact.kind is FactKind.CLASS_DEF:
                artifact = self._class_artifact(fact, context.repo_id)
            elif fact.kind is FactKind.SYMBOL:
                artifact = self._symbol_artifact(fact, context.repo_id)
            else:
                continue
            if artifact is None or artifact.id in seen:
                continue
            seen.add(artifact.id)
            out.append(artifact)
        return out

    def _class_artifact(self, fact: Fact, repo_id: str) -> CodeArtifact | None:
        if _is_test_path(fact.file):
            return None
        name = str(fact.data.get("name", ""))
        if not name:
            return None
        return CodeArtifact(
            id=f"class:{repo_id}:{_rel(fact.file)}:{name}",
            repoId=repo_id,
            type="class",
            name=name,
            file=_rel(fact.file),
            lineRange=LineRange(start=fact.line, end=fact.line_end or fact.line),
            isPublic=not name.startswith("_"),
        )

    def _symbol_artifact(self, fact: Fact, repo_id: str) -> CodeArtifact | None:
        if _is_test_path(fact.file):
            return None
        sym_kind = str(fact.data.get("sym_kind", ""))
        name = str(fact.data.get("name", ""))
        if not name:
            return None
        enclosing = str(fact.data.get("enclosing_class", ""))
        if sym_kind == "function":
            type_label = "function"
            artifact_id = f"fn:{repo_id}:{_rel(fact.file)}:{name}"
        elif sym_kind == "method":
            type_label = "method"
            # Methods get qualified by their enclosing class to keep IDs
            # unique when the same method name lives on multiple classes.
            artifact_id = f"method:{repo_id}:{_rel(fact.file)}:{enclosing}.{name}"
        elif sym_kind == "field":
            # Fields are tracked as facts but not emitted as artifacts;
            # the impact engine doesn't query against them.
            return None
        else:
            return None
        return CodeArtifact(
            id=artifact_id,
            repoId=repo_id,
            type=type_label,
            name=name,
            file=_rel(fact.file),
            lineRange=LineRange(start=fact.line, end=fact.line_end or fact.line),
            isPublic=not name.startswith("_"),
        )


def _is_test_path(file: str) -> bool:
    parts = Path(file).parts
    return any(part in _EXCLUDED_PATH_PARTS for part in parts)


def _rel(file: str) -> str:
    """Return file path as-is; the adapter rewrites to repo-relative paths
    after collection."""
    return file
