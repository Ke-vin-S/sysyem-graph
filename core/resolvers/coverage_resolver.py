"""CoverageResolver: link TestCase → CodeArtifact via import statements.

Without instrumented runtime data, the cheapest heuristic for "this test
exercises this artifact" is the test file's import list. If a test does
`from src.routers.charges import get_charge`, it covers the `get_charge`
artifact in `src/routers/charges.py`.

Algorithm:
  1. For each TestCase, collect IMPORT facts in its file.
  2. For each import, derive candidate target files (`module_to_file`) and
     optional symbol names (`from X import Y, Z`).
  3. Match against the artifact catalog; emit (test_id, artifact_id) edges.
  4. Update each TestCase with the resolved covers_artifacts tuple.

Returns *new* TestCase instances (TestCase is frozen). Callers replace the
old records with the returned ones.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from core.facts import Fact, FactKind, FactTree
from core.types import CodeArtifact, TestCase


@dataclass
class CoverageEdge:
    """Explicit (test → artifact) edge, ready for Neo4j as COVERS."""

    test_id: str
    artifact_id: str
    reason: str
    """Why the resolver linked these — `import_module`, `import_from`, etc."""


@dataclass
class CoverageResolution:
    tests: list[TestCase]
    """TestCases with `covers_artifacts` populated."""

    edges: list[CoverageEdge]
    """Explicit edge list. Same information as tests[i].covers_artifacts,
    but with reasons attached and one record per edge — easier to feed into
    Neo4j or to audit."""


class CoverageResolver:
    def resolve(
        self,
        *,
        tree: FactTree,
        tests: Iterable[TestCase],
        artifacts: Iterable[CodeArtifact],
        repo_root: str | None = None,
    ) -> CoverageResolution:
        """`repo_root` is used to translate absolute fact-file paths back to
        the repo-relative paths that artifacts carry. Omit for tests where
        both sides already share the same path shape."""
        artifacts_list = list(artifacts)
        by_file: dict[str, list[CodeArtifact]] = {}
        for artifact in artifacts_list:
            by_file.setdefault(artifact.file, []).append(artifact)

        updated_tests: list[TestCase] = []
        edges: list[CoverageEdge] = []

        for test in tests:
            test_file = _rel_to(test.file, repo_root) if repo_root else test.file
            imports = self._imports_for_test_file(tree, test_file, repo_root)
            covered: set[str] = set()
            for imp in imports:
                module = str(imp.data.get("module", ""))
                names = list(imp.data.get("names") or [])
                for candidate_file in _module_to_candidate_files(module):
                    bucket = by_file.get(candidate_file, [])
                    if not bucket:
                        continue
                    if names:
                        # `from X import a, b` → match artifacts named `a` or `b`
                        wanted = set(names)
                        for artifact in bucket:
                            if artifact.name in wanted:
                                covered.add(artifact.id)
                                edges.append(
                                    CoverageEdge(
                                        test_id=test.id,
                                        artifact_id=artifact.id,
                                        reason=f"from {module} import {artifact.name}",
                                    )
                                )
                        # Also match a class/module-level artifact if the
                        # imported name IS the module's class/submodule.
                    else:
                        # `import X` → covers everything declared in that module
                        for artifact in bucket:
                            covered.add(artifact.id)
                            edges.append(
                                CoverageEdge(
                                    test_id=test.id,
                                    artifact_id=artifact.id,
                                    reason=f"import {module}",
                                )
                            )

            if covered:
                updated_tests.append(test.model_copy(update={"covers_artifacts": tuple(sorted(covered))}))
            else:
                updated_tests.append(test)

        return CoverageResolution(tests=updated_tests, edges=edges)

    def _imports_for_test_file(
        self, tree: FactTree, test_file: str, repo_root: str | None
    ) -> list[Fact]:
        """Return IMPORT facts for the file containing this test.

        TestCases carry repo-relative file paths; IMPORT facts carry whatever
        path the walker saw (typically absolute when walked under a temp dir).
        We match by suffix so both shapes work.
        """
        out: list[Fact] = []
        for fact in tree.where(kind=FactKind.IMPORT):
            fact_file = fact.file
            if repo_root is not None:
                fact_file = _rel_to(fact_file, repo_root)
            if fact_file == test_file or fact_file.endswith("/" + test_file):
                out.append(fact)
        return out


def _module_to_candidate_files(module: str) -> list[str]:
    """`src.routers.charges` -> ['src/routers/charges.py',
                                  'src/routers/charges/__init__.py']

    Returns an ordered list of candidates; first match wins downstream.
    Empty/invalid input returns []. Leading dots (relative imports without a
    package anchor) are stripped — we don't have enough info to resolve them
    properly here, and resolver consumers fall back to file-suffix matching.
    """
    if not module:
        return []
    cleaned = module.lstrip(".")
    if not cleaned:
        return []
    base = cleaned.replace(".", "/")
    return [f"{base}.py", f"{base}/__init__.py"]


def _rel_to(file: str, root: str) -> str:
    """Return `file` relative to `root`, normalized with forward slashes.

    Falls back to the original path when the file isn't actually under root
    (e.g. site-packages imports show up as absolute paths we can't relativize).
    """
    if not root:
        return file
    fp = PurePosixPath(file.replace("\\", "/"))
    rp = PurePosixPath(root.replace("\\", "/"))
    try:
        return str(fp.relative_to(rp))
    except ValueError:
        # Try a more lenient match for paths that share a tail
        parts = fp.parts
        root_name = rp.name
        if root_name in parts:
            idx = parts.index(root_name)
            return str(PurePosixPath(*parts[idx + 1 :]))
        return file
