"""Turn a RepoSnapshot into Service + CodeArtifact records.

CodeArtifact extraction here is intentionally cheap: we identify endpoint
declarations and top-level function defs by pattern matching, not full AST.
The `testparser` adapter does deeper AST work; this one is just enough to
populate the graph with public-API surface.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

from core.types import CodeArtifact, LineRange, Service
from ingestion.adapters.github.client import RepoFile, RepoSnapshot

# Common HTTP framework decorator patterns. Not exhaustive, but cheap and
# good enough for Phase 1 — the testparser adapter does the real AST work.
_HTTP_DECORATORS = re.compile(
    r"""
    @(?:app|router|api|blueprint)\.
    (?P<method>get|post|put|delete|patch|head|options|route)
    \s*\(\s*(?P<args>[^)]*)\)
    """,
    re.VERBOSE | re.IGNORECASE,
)

_PY_FUNCDEF = re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_][\w]*)\s*\(", re.MULTILINE)


def service_from_snapshot(snapshot: RepoSnapshot) -> Service:
    return Service(
        id=snapshot.full_name,
        name=snapshot.full_name.split("/")[-1],
        repoUrl=snapshot.clone_url or f"https://github.com/{snapshot.full_name}",
        language=snapshot.language or "unknown",
        framework="unknown",
        owner=snapshot.owner or "unknown",
        createdAt=snapshot.created_at,
        lastUpdatedAt=snapshot.pushed_at,
        isActive=True,
    )


def artifacts_from_snapshot(snapshot: RepoSnapshot) -> list[CodeArtifact]:
    out: list[CodeArtifact] = []
    for f in snapshot.files:
        if f.content is None:
            continue
        out.extend(_endpoint_artifacts(snapshot.full_name, f))
        if f.path.endswith(".py") and not _looks_like_test(f.path):
            out.extend(_python_function_artifacts(snapshot.full_name, f))
    return out


def _endpoint_artifacts(repo_id: str, file: RepoFile) -> Iterable[CodeArtifact]:
    if file.content is None:
        return
    for match in _HTTP_DECORATORS.finditer(file.content):
        path = _extract_path(match.group("args"))
        if path is None:
            continue
        method = match.group("method").upper()
        if method == "ROUTE":
            method = _extract_method(match.group("args")) or "*"
        line_start = file.content.count("\n", 0, match.start()) + 1
        yield CodeArtifact(
            id=f"endpoint:{repo_id}:{method}:{path}",
            repoId=repo_id,
            type="endpoint",
            name=f"{method} {path}",
            file=file.path,
            lineRange=LineRange(start=line_start, end=line_start),
            isPublic=True,
        )


def _python_function_artifacts(repo_id: str, file: RepoFile) -> Iterable[CodeArtifact]:
    if file.content is None:
        return
    for match in _PY_FUNCDEF.finditer(file.content):
        name = match.group("name")
        if name.startswith("_"):
            continue
        line_start = file.content.count("\n", 0, match.start()) + 1
        yield CodeArtifact(
            id=f"fn:{repo_id}:{file.path}:{name}",
            repoId=repo_id,
            type="function",
            name=name,
            file=file.path,
            lineRange=LineRange(start=line_start, end=line_start),
            isPublic=True,
        )


_PATH_LITERAL = re.compile(r"""['"]([^'"]+)['"]""")
_METHODS_LIST = re.compile(r"methods\s*=\s*\[([^\]]+)\]", re.IGNORECASE)


def _extract_path(args: str) -> str | None:
    match = _PATH_LITERAL.search(args)
    if not match:
        return None
    path = match.group(1)
    if not path.startswith("/"):
        return None
    return path


def _extract_method(args: str) -> str | None:
    match = _METHODS_LIST.search(args)
    if not match:
        return None
    methods = _PATH_LITERAL.findall(match.group(1))
    return methods[0].upper() if methods else None


def _looks_like_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in path


class RepoFetcher:
    """Convenience facade combining service + artifact extraction.

    Holds no state; exists so callers can substitute a different extraction
    strategy in tests without monkeypatching free functions.
    """

    def to_records(self, snapshot: RepoSnapshot) -> tuple[Service, list[CodeArtifact]]:
        service = service_from_snapshot(snapshot)
        artifacts = artifacts_from_snapshot(snapshot)
        return service, artifacts

    @staticmethod
    def now() -> datetime:
        from datetime import timezone

        return datetime.now(timezone.utc)
