"""CoverageResolver: link TestCase → CodeArtifact via import statements.

Without instrumented runtime data, the cheapest heuristic for "this test
exercises this artifact" is the test file's import list. If a test does
`from src.routers.charges import get_charge`, it covers the `get_charge`
artifact in `src/routers/charges.py`.

Language-aware: the resolver consults a `LanguageLibrary` to determine
  * how dotted modules expand to candidate file paths (Python: ".py" + "/__init__.py",
    Java: ".java", PL/SQL: ".pks"/".pkb"/".sql")
  * which files act as package aggregators / re-exporters (Python: __init__.py,
    TypeScript: index.ts, PL/SQL: *.pks)
  * how to reverse a package aggregator file to a module name

When no library is passed, a fallback profile preserves the original Python
semantics so existing unit tests work unchanged.

Algorithm:
  1. Pre-build an alias map from every aggregator file's imports so we can
     follow re-export patterns. When `core/resolvers/__init__.py` does
     `from core.resolvers.endpoint_resolver import EndpointResolver`,
     a test that writes `from core.resolvers import EndpointResolver`
     still resolves to the artifact in `endpoint_resolver.py`.
  2. For each TestCase, collect IMPORT facts in its file.
  3. For each import, expand the (module, name) pair through the alias map.
  4. Map each expanded module to candidate file paths and match by name.
  5. Update each TestCase with the resolved covers_artifacts tuple.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from core.facts import Fact, FactKind, FactTree
from core.languages import (
    LanguageLibrary,
    LanguageProfile,
)
from core.languages.profile import (
    Grammar,
    GrammarKind,
    ModuleResolution,
    PackageAggregator,
)
from core.languages.resolution import (
    init_file_to_module,
    is_aggregator_file,
    resolve_candidate_files,
)
from core.types import CodeArtifact, TestCase


@dataclass
class CoverageEdge:
    """Explicit (test → artifact) edge, ready for Neo4j as COVERS."""

    test_id: str
    artifact_id: str
    reason: str


@dataclass
class CoverageResolution:
    tests: list[TestCase]
    edges: list[CoverageEdge]


# (module, name) pair. Same shape across languages.
_AliasKey = tuple[str, str]


#: Fallback used when no LanguageLibrary is passed. Mirrors the Python
#: knowledge that used to be hardcoded.
_FALLBACK_PYTHON = LanguageProfile(
    name="python",
    file_extensions=(".py",),
    grammar=Grammar(kind=GrammarKind.NATIVE, driver="core.languages.python.grammar.PythonGrammar"),
    module_resolution=ModuleResolution(
        separator=".",
        candidate_path_templates=("{module}.py", "{module}/__init__.py"),
    ),
    package_aggregator=PackageAggregator(files=("__init__.py",)),
)


class CoverageResolver:
    _MAX_ALIAS_DEPTH = 5

    def resolve(
        self,
        *,
        tree: FactTree,
        tests: Iterable[TestCase],
        artifacts: Iterable[CodeArtifact],
        repo_root: str | None = None,
        languages: LanguageLibrary | None = None,
    ) -> CoverageResolution:
        artifacts_list = list(artifacts)
        by_file: dict[str, list[CodeArtifact]] = {}
        for artifact in artifacts_list:
            by_file.setdefault(artifact.file, []).append(artifact)

        alias_map = self._build_alias_map(tree, repo_root, languages)
        references_index = self._build_references_index(tree, repo_root)

        updated_tests: list[TestCase] = []
        edges: list[CoverageEdge] = []

        for test in tests:
            test_file = _rel_to(test.file, repo_root) if repo_root else test.file
            test_profile = self._profile_for(test_file, languages)
            imports = self._imports_for_test_file(tree, test_file, repo_root)
            # Per-test name filter (Python: from SYMBOL.references; Java: None
            # for now, falls back to file-scoped). `None` means "no filter
            # known, keep old file-scoped behavior".
            test_references: frozenset[str] | None = references_index.get(
                (test_file, test.name)
            )
            covered: set[str] = set()
            for imp in imports:
                module = str(imp.data.get("module", ""))
                names = list(imp.data.get("names") or [])
                if names:
                    for name in names:
                        if test_references is not None and name not in test_references:
                            continue  # imported but not touched by this test
                        for real_module, real_name in self._expand(module, name, alias_map):
                            self._match_named(
                                real_module,
                                real_name,
                                source_module=module,
                                profile=test_profile,
                                by_file=by_file,
                                test=test,
                                covered=covered,
                                edges=edges,
                            )
                else:
                    # `import X` (bare) — no name-level signal, keep
                    # file-scoped semantics. Tighten later if needed.
                    for candidate_file in resolve_candidate_files(module, test_profile):
                        for artifact in by_file.get(candidate_file, []):
                            covered.add(artifact.id)
                            edges.append(
                                CoverageEdge(
                                    test_id=test.id,
                                    artifact_id=artifact.id,
                                    reason=f"import {module}",
                                )
                            )

            if covered:
                updated_tests.append(
                    test.model_copy(update={"covers_artifacts": tuple(sorted(covered))})
                )
            else:
                updated_tests.append(test)

        return CoverageResolution(tests=updated_tests, edges=edges)

    # -- references index -----------------------------------------------

    def _build_references_index(
        self, tree: FactTree, repo_root: str | None
    ) -> dict[tuple[str, str], frozenset[str]]:
        """`(file, function_name) -> {referenced identifiers}` from SYMBOL facts.

        Only function/method symbols whose grammar populated
        `data["references"]` participate. Tests in languages whose grammar
        doesn't emit references (Java today) won't appear here, and the
        resolver falls back to file-scoped coverage for them.
        """
        index: dict[tuple[str, str], frozenset[str]] = {}
        for fact in tree.where(kind=FactKind.SYMBOL):
            refs = fact.data.get("references")
            if refs is None:
                continue
            name = str(fact.data.get("name", ""))
            if not name:
                continue
            file = _rel_to(fact.file, repo_root) if repo_root else fact.file
            index[(file, name)] = frozenset(str(r) for r in refs)
        return index

    # -- profile selection ----------------------------------------------

    def _profile_for(
        self, file: str, languages: LanguageLibrary | None
    ) -> LanguageProfile:
        if languages is not None:
            profile = languages.for_file(file)
            if profile is not None:
                return profile
        return _FALLBACK_PYTHON

    # -- alias map -------------------------------------------------------

    def _build_alias_map(
        self,
        tree: FactTree,
        repo_root: str | None,
        languages: LanguageLibrary | None,
    ) -> dict[_AliasKey, list[_AliasKey]]:
        """Build (package_module, name) -> [(real_module, real_name), ...]
        from every package-aggregator file's IMPORT facts.

        Aggregator files are language-specific (Python: __init__.py,
        TS: index.ts, PL/SQL: *.pks). The resolver looks up the file's
        language profile to decide.
        """
        aliases: dict[_AliasKey, list[_AliasKey]] = {}
        for fact in tree.where(kind=FactKind.IMPORT):
            file = _rel_to(fact.file, repo_root) if repo_root else fact.file
            profile = self._profile_for(file, languages)
            if not is_aggregator_file(file, profile):
                continue
            package_module = init_file_to_module(file, profile)
            if not package_module:
                continue
            target_module = self._resolve_target_module(fact, package_module, profile)
            if not target_module:
                continue
            for name in fact.data.get("names") or []:
                if not name:
                    continue
                aliases.setdefault((package_module, name), []).append(
                    (target_module, name)
                )
        return aliases

    def _resolve_target_module(
        self, import_fact: Fact, package_module: str, profile: LanguageProfile
    ) -> str:
        """Resolve absolute and relative imports into a canonical module string."""
        target_module = str(import_fact.data.get("module", ""))
        level = int(import_fact.data.get("level", 0) or 0)
        if level <= 0:
            return target_module
        sep = profile.module_resolution.separator or "."
        parts = package_module.split(sep)
        if level > len(parts):
            return target_module
        anchor = sep.join(parts[: len(parts) - level + 1])
        if target_module:
            return f"{anchor}{sep}{target_module}"
        return anchor

    def _expand(
        self,
        module: str,
        name: str,
        alias_map: dict[_AliasKey, list[_AliasKey]],
    ) -> Iterable[_AliasKey]:
        seen: set[_AliasKey] = set()
        stack: list[tuple[str, str, int]] = [(module, name, 0)]
        while stack:
            mod, nm, depth = stack.pop()
            key = (mod, nm)
            if key in seen or depth > self._MAX_ALIAS_DEPTH:
                continue
            seen.add(key)
            yield key
            for next_mod, next_name in alias_map.get(key, []):
                stack.append((next_mod, next_name, depth + 1))

    # -- match helper ---------------------------------------------------

    def _match_named(
        self,
        module: str,
        name: str,
        *,
        source_module: str,
        profile: LanguageProfile,
        by_file: dict[str, list[CodeArtifact]],
        test: TestCase,
        covered: set[str],
        edges: list[CoverageEdge],
    ) -> None:
        for candidate_file in resolve_candidate_files(module, profile):
            for artifact in by_file.get(candidate_file, []):
                if artifact.name != name:
                    continue
                if artifact.id in covered:
                    continue
                covered.add(artifact.id)
                reason = (
                    f"from {source_module} import {name}"
                    if module == source_module
                    else f"from {source_module} import {name} (re-exported via {module})"
                )
                edges.append(
                    CoverageEdge(
                        test_id=test.id,
                        artifact_id=artifact.id,
                        reason=reason,
                    )
                )

    def _imports_for_test_file(
        self, tree: FactTree, test_file: str, repo_root: str | None
    ) -> list[Fact]:
        out: list[Fact] = []
        for fact in tree.where(kind=FactKind.IMPORT):
            fact_file = fact.file
            if repo_root is not None:
                fact_file = _rel_to(fact_file, repo_root)
            if fact_file == test_file or fact_file.endswith("/" + test_file):
                out.append(fact)
        return out


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
