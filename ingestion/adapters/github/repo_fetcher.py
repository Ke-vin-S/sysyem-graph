"""Turn an on-disk clone into Service + CodeArtifact records.

The clone-based GitHub adapter calls `to_records_from_path(root, …)` —
no in-memory snapshots, no temp dirs. We delegate to the same
Walker + Grammars + Resolvers stack the testparser adapter uses so
endpoint reconstruction stays cross-file-aware (prefix chains, config
base paths, class-level annotations).

The emit surface is preserved from the prior REST-based implementation:
one `Service` plus a list of `CodeArtifact`s of `type=endpoint` and
`type=function`. IDs match the prior format so re-ingest is idempotent
in Neo4j (loader MERGEs on `id`).
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.facts import FactKind
from core.frameworks import compose, detect_frameworks, load_library
from core.frameworks.library import DEFAULT_FRAMEWORKS_DIR, FrameworkLibrary
from core.resolvers import EndpointResolver, ResolverContext
from core.types import CodeArtifact, LineRange, Service
from core.walker import Walker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepoMetadata:
    """Repo-level facts derived from a local clone — used to build the
    `Service` record. Free of any GitHub REST API dependency."""

    full_name: str
    """Canonical `owner/name`."""
    repo_url: str
    """`https://github.com/owner/name` (whatever we registered as)."""
    owner: str
    language: str
    created_at: datetime
    last_updated_at: datetime


class RepoFetcher:
    """Build Service + CodeArtifact records from a cloned repository.

    Lifecycle:
        fetcher = RepoFetcher()
        service, artifacts = fetcher.to_records_from_path(
            clone_path, repo_id="owner/name", repo_url="https://…"
        )
    """

    def __init__(
        self,
        *,
        walker: Walker | None = None,
        resolver: EndpointResolver | None = None,
        library: FrameworkLibrary | None = None,
    ) -> None:
        self._walker = walker or Walker()
        self._resolver = resolver or EndpointResolver()
        self._library = library or load_library(DEFAULT_FRAMEWORKS_DIR)

    def to_records_from_path(
        self,
        root: Path,
        *,
        repo_id: str,
        repo_url: str = "",
    ) -> tuple[Service, list[CodeArtifact]]:
        meta = derive_metadata(root, repo_id=repo_id, repo_url=repo_url)
        service = service_from_metadata(meta)
        artifacts = self._artifacts(root, repo_id=repo_id)
        return service, artifacts

    def _artifacts(self, root: Path, *, repo_id: str) -> list[CodeArtifact]:
        tree = self._walker.walk(root, repo_id=repo_id)
        detected = detect_frameworks(tree, self._library)
        effective = tuple(compose(fw, None) for fw in detected)
        endpoints = self._resolver.resolve(
            ResolverContext(tree=tree, frameworks=effective, repo_id=repo_id)
        )

        artifacts: list[CodeArtifact] = []
        for endpoint in endpoints:
            rel_file = _relative_to(endpoint.handler_file, root)
            artifacts.append(
                CodeArtifact(
                    id=_endpoint_id(repo_id, endpoint.method, endpoint.full_path),
                    repoId=repo_id,
                    type="endpoint",
                    name=f"{endpoint.method} {endpoint.full_path}",
                    file=rel_file,
                    lineRange=LineRange(start=1, end=1),
                    isPublic=True,
                )
            )

        # Public top-level functions become `function` CodeArtifacts (same
        # contract as the previous REST-based path).
        for symbol in tree.where(kind=FactKind.SYMBOL):
            if symbol.data.get("sym_kind") != "function":
                continue
            name = str(symbol.data.get("name", ""))
            if not name or name.startswith("_"):
                continue
            if symbol.data.get("enclosing_class"):
                continue
            rel_file = _relative_to(symbol.file, root)
            artifacts.append(
                CodeArtifact(
                    id=f"fn:{repo_id}:{rel_file}:{name}",
                    repoId=repo_id,
                    type="function",
                    name=name,
                    file=rel_file,
                    lineRange=LineRange(
                        start=symbol.line,
                        end=symbol.line_end or symbol.line,
                    ),
                    isPublic=True,
                )
            )
        return artifacts


# ---- metadata derivation ---------------------------------------------------


def service_from_metadata(meta: RepoMetadata) -> Service:
    return Service(
        id=meta.full_name,
        name=meta.full_name.split("/")[-1],
        repoUrl=meta.repo_url or f"https://github.com/{meta.full_name}",
        language=meta.language or "unknown",
        framework="unknown",
        owner=meta.owner or "unknown",
        createdAt=meta.created_at,
        lastUpdatedAt=meta.last_updated_at,
        isActive=True,
    )


def derive_metadata(root: Path, *, repo_id: str, repo_url: str = "") -> RepoMetadata:
    """Pull lightweight repo facts straight from the clone — no API.

    `repo_id` is treated as `owner/name`; this is the canonical Service.id
    used elsewhere in the graph."""
    owner = repo_id.split("/", 1)[0] if "/" in repo_id else "unknown"
    created = _git_first_commit_at(root) or datetime.now(timezone.utc)
    updated = _git_last_commit_at(root) or created
    language = _dominant_language(root)
    return RepoMetadata(
        full_name=repo_id,
        repo_url=repo_url,
        owner=owner,
        language=language,
        created_at=created,
        last_updated_at=updated,
    )


def _git_first_commit_at(root: Path) -> datetime | None:
    """Date of the earliest reachable commit. On a `--depth=1` clone this
    is the same as the latest commit (shallow), which is fine — we just
    need *some* timestamp."""
    return _git_rev_date(root, ["log", "--max-parents=0", "-1", "--format=%aI"])


def _git_last_commit_at(root: Path) -> datetime | None:
    return _git_rev_date(root, ["log", "-1", "--format=%aI"])


def _git_rev_date(root: Path, args: list[str]) -> datetime | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return datetime.fromisoformat(proc.stdout.strip())
    except ValueError:
        return None


# Crude but effective: dominant language = most common source extension.
_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rb": "ruby",
    ".rs": "rust",
    ".cs": "csharp",
}


def _dominant_language(root: Path) -> str:
    counts: dict[str, int] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # Skip the usual vendor/cache noise so a Python repo's
        # `node_modules` doesn't accidentally win.
        if any(part in {".git", "node_modules", ".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        lang = _LANG_BY_EXT.get(path.suffix.lower())
        if lang is None:
            continue
        counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return "unknown"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _endpoint_id(repo_id: str, method: str, path: str) -> str:
    return f"endpoint:{repo_id}:{method}:{path}"


def _relative_to(file: str, root: Path) -> str:
    try:
        return os.path.relpath(file, root)
    except ValueError:
        return file
