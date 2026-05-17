"""Turn a RepoSnapshot into Service + CodeArtifact records.

This module used to do its own regex-based artifact extraction. It now
delegates to the same Walker + Grammars + Resolvers stack that the
testparser adapter uses, so endpoint reconstruction is cross-file-aware
(prefix chains, config base paths, class-level annotations).

The contract is unchanged: `RepoFetcher.to_records(snapshot) -> (Service,
list[CodeArtifact])`. What changes is that endpoint artifacts now carry
full reconstructed paths instead of just whatever a single regex matched.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from core.facts import FactKind
from core.frameworks import compose, detect_frameworks, load_library
from core.frameworks.library import DEFAULT_FRAMEWORKS_DIR, FrameworkLibrary
from core.resolvers import EndpointResolver, ResolverContext
from core.types import CodeArtifact, LineRange, Service
from core.walker import Walker
from ingestion.adapters.github.client import RepoFile, RepoSnapshot

logger = logging.getLogger(__name__)


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


class RepoFetcher:
    """Build Service + CodeArtifact records from a RepoSnapshot.

    Internally writes the snapshot's files to a temp dir and runs the Walker
    over them. This is cheap (snapshots are filtered to source files only)
    and avoids duplicating the walker/grammar/resolver logic just for the
    GitHub path.
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

    def to_records(self, snapshot: RepoSnapshot) -> tuple[Service, list[CodeArtifact]]:
        service = service_from_snapshot(snapshot)
        artifacts = self._artifacts(snapshot)
        return service, artifacts

    def _artifacts(self, snapshot: RepoSnapshot) -> list[CodeArtifact]:
        with tempfile.TemporaryDirectory(prefix="sg-github-") as tmp:
            root = Path(tmp)
            for repo_file in snapshot.files:
                if repo_file.content is None:
                    continue
                path = root / repo_file.path
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    path.write_text(repo_file.content, encoding="utf-8")
                except OSError as exc:
                    logger.warning("repo_fetcher: write failed for %s: %s", repo_file.path, exc)

            tree = self._walker.walk(root, repo_id=snapshot.full_name)
            detected = detect_frameworks(tree, self._library)
            effective = tuple(compose(fw, None) for fw in detected)
            endpoints = self._resolver.resolve(
                ResolverContext(tree=tree, frameworks=effective, repo_id=snapshot.full_name)
            )

            artifacts: list[CodeArtifact] = []
            for endpoint in endpoints:
                # Normalize the handler file path back to repo-relative.
                rel_file = _relative_to(endpoint.handler_file, root)
                artifacts.append(
                    CodeArtifact(
                        id=_endpoint_id(snapshot.full_name, endpoint.method, endpoint.full_path),
                        repoId=snapshot.full_name,
                        type="endpoint",
                        name=f"{endpoint.method} {endpoint.full_path}",
                        file=rel_file,
                        lineRange=LineRange(start=1, end=1),
                        isPublic=True,
                    )
                )

            # Public top-level functions (no leading underscore, no enclosing
            # class) become `function` CodeArtifacts. This used to live in a
            # regex-based extractor; we now drive it off SYMBOL facts.
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
                        id=f"fn:{snapshot.full_name}:{rel_file}:{name}",
                        repoId=snapshot.full_name,
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

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


def _endpoint_id(repo_id: str, method: str, path: str) -> str:
    return f"endpoint:{repo_id}:{method}:{path}"


def _relative_to(file: str, root: Path) -> str:
    try:
        return os.path.relpath(file, root)
    except ValueError:
        return file
