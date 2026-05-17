"""TestResolver: facts + framework YAML -> TestCase records.

Reproduces the classification logic that used to live in `python_parser.py`,
`java_parser.py`, and `classifier.py`. The difference: the rules now come
from `frameworks/*.yaml`, not from hardcoded constants in each parser.

Output is `core.types.TestCase`, which `TestParserAdapter` returns directly
so Phase 2 (Neo4j) keeps consuming the same shape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from core.facts import Fact, FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.resolvers.resolver import ResolverContext
from core.types import LineRange, TestCase, TestType

logger = logging.getLogger(__name__)


@dataclass
class ResolvedTest:
    """Internal pre-TestCase record so the resolver can attach derivation."""

    test: TestCase
    derivation: tuple[str, ...]


class TestResolver:
    def resolve(self, context: ResolverContext) -> list[TestCase]:
        # Build a per-language merged view: function name prefixes, decorator
        # callees, integration/e2e markers, mock annotations, external modules.
        merged = _merge_test_rules(context.frameworks)
        tree = context.tree

        # Map import-module -> file, used to know which modules a file imports
        # without re-walking the tree per symbol.
        imports_by_file = _imports_by_file(tree)
        mocked_by_file = _mocked_modules_by_file(
            tree,
            merged.mock_decorator_callees,
            merged.mock_field_annotations,
            imports_by_file,
        )

        results: list[TestCase] = []
        for symbol in tree.where(kind=FactKind.SYMBOL):
            sym_kind = symbol.data.get("sym_kind", "")
            if sym_kind not in ("function", "method"):
                continue
            name = symbol.data.get("name", "")
            decorators = _decorator_callees_for_symbol(tree, symbol)

            if not _is_test(name, decorators, merged):
                continue

            test_type = _classify(
                file=symbol.file,
                decorators=decorators,
                imports=imports_by_file.get(symbol.file, frozenset()),
                mocked=mocked_by_file.get(symbol.file, frozenset()),
                merged=merged,
            )

            line_end = symbol.line_end or symbol.line
            # TestResolver emits raw fact paths; the adapter rewrites them to
            # repo-relative. Keeping the relativization in one place (adapter)
            # avoids the ID/file mismatch we hit when both layers tried.
            test = TestCase(
                id=f"test:{context.repo_id}:{symbol.file}:{name}",
                repoId=context.repo_id,
                type=test_type,
                name=name,
                file=symbol.file,
                lineRange=LineRange(start=symbol.line, end=line_end),
                duration_ms=0,
                flakiness_score=0.0,
                priority="HIGH" if test_type is TestType.INTEGRATION else "MEDIUM",
                affectedRepos=(context.repo_id,),
            )
            results.append(test)
        return results


@dataclass
class _MergedTestRules:
    function_name_prefixes: frozenset[str]
    decorator_callees: frozenset[str]
    integration_markers: frozenset[str]
    e2e_markers: frozenset[str]
    test_path_globs: tuple[str, ...]
    test_path_prefixes: tuple[tuple[str, str], ...]
    external_modules: frozenset[str]
    mock_decorator_callees: frozenset[str]
    mock_field_annotations: frozenset[str]


def _merge_test_rules(frameworks: tuple[EffectiveFramework, ...]) -> _MergedTestRules:
    fn_prefixes: set[str] = set()
    decorator_callees: set[str] = set()
    integration_markers: set[str] = set()
    e2e_markers: set[str] = set()
    test_path_globs: list[str] = []
    test_path_prefixes: list[tuple[str, str]] = []
    external_modules: set[str] = set()
    mock_decorator_callees: set[str] = set()
    mock_field_annotations: set[str] = set()

    for fw in frameworks:
        if fw.tests is not None:
            fn_prefixes.update(fw.tests.function_name_prefixes)
            decorator_callees.update(fw.tests.decorator_callees)
            integration_markers.update(fw.tests.integration_markers)
            e2e_markers.update(fw.tests.e2e_markers)
            test_path_globs.extend(fw.tests.test_path_globs)
            for entry in fw.tests.test_path_prefixes:
                parts = entry.split("/")
                if len(parts) >= 2:
                    test_path_prefixes.append((parts[-2], parts[-1]))
        if fw.http_clients is not None:
            external_modules.update(fw.http_clients.external_modules)
        if fw.mocks is not None:
            mock_decorator_callees.update(fw.mocks.decorator_callees)
            mock_decorator_callees.update(fw.mocks.with_callees)
            mock_field_annotations.update(fw.mocks.field_annotations)

    return _MergedTestRules(
        function_name_prefixes=frozenset(fn_prefixes),
        decorator_callees=frozenset(decorator_callees),
        integration_markers=frozenset(integration_markers),
        e2e_markers=frozenset(e2e_markers),
        test_path_globs=tuple(test_path_globs),
        test_path_prefixes=tuple(test_path_prefixes),
        external_modules=frozenset(external_modules),
        mock_decorator_callees=frozenset(mock_decorator_callees),
        mock_field_annotations=frozenset(mock_field_annotations),
    )


def _is_test(name: str, decorators: set[str], merged: _MergedTestRules) -> bool:
    if any(name.startswith(prefix) for prefix in merged.function_name_prefixes):
        return True
    if decorators & merged.decorator_callees:
        return True
    return False


def _classify(
    *,
    file: str,
    decorators: set[str],
    imports: frozenset[str],
    mocked: frozenset[str],
    merged: _MergedTestRules,
) -> TestType:
    path_parts = Path(file).parts

    if decorators & merged.e2e_markers or _has_segment(path_parts, "tests", "e2e"):
        return TestType.E2E

    if decorators & merged.integration_markers:
        return TestType.INTEGRATION

    unmocked_externals = imports & merged.external_modules - mocked
    if unmocked_externals:
        return TestType.INTEGRATION

    if _has_segment(path_parts, "tests", "integration"):
        return TestType.INTEGRATION

    if _has_segment(path_parts, "tests", "component"):
        return TestType.COMPONENT

    return TestType.UNIT


def _has_segment(parts: tuple[str, ...], a: str, b: str) -> bool:
    for i in range(len(parts) - 1):
        if parts[i] == a and parts[i + 1] == b:
            return True
    return False


def _decorator_callees_for_symbol(tree: FactTree, symbol: Fact) -> set[str]:
    out: set[str] = set()
    name = symbol.data.get("name", "")
    for dec in tree.where(kind=FactKind.DECORATOR, file=symbol.file):
        if dec.data.get("target_symbol") == name:
            callee = dec.data.get("callee", "")
            if callee:
                out.add(callee)
    for ann in tree.where(kind=FactKind.ANNOTATION, file=symbol.file):
        if ann.data.get("target_symbol") == name:
            callee = ann.data.get("callee", "")
            if callee:
                out.add(callee)
    return out


def _imports_by_file(tree: FactTree) -> dict[str, frozenset[str]]:
    out: dict[str, set[str]] = {}
    for imp in tree.where(kind=FactKind.IMPORT):
        module = str(imp.data.get("module", ""))
        if not module:
            continue
        head = module.split(".", 1)[0]
        out.setdefault(imp.file, set()).add(head)
        out[imp.file].add(module)
    return {file: frozenset(modules) for file, modules in out.items()}


def _mocked_modules_by_file(
    tree: FactTree,
    mock_decorator_callees: frozenset[str],
    mock_field_annotations: frozenset[str],
    imports_by_file: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    """Best-effort: collect modules that look mocked in each file.

    For Python: `@patch("x.y.z")` first arg is a dotted path; we keep the
    top-level module name as "mocked".

    For Java: `@Mock` field annotations carry a target type (simple name).
    We resolve that simple name against the file's imports so the *module*
    that brought in the type (`okhttp3` for `OkHttpClient`) is what ends up
    in the mocked set — that's what the external-modules check is keyed on.
    """
    out: dict[str, set[str]] = {}
    for dec in tree.where(kind=FactKind.DECORATOR):
        callee = dec.data.get("callee", "")
        if callee not in mock_decorator_callees:
            continue
        args = dec.data.get("args") or []
        if args and isinstance(args[0], str):
            top = args[0].split(".", 1)[0]
            out.setdefault(dec.file, set()).add(top)
    for ann in tree.where(kind=FactKind.ANNOTATION):
        callee = ann.data.get("callee", "")
        if callee not in mock_field_annotations:
            continue
        target_type = ann.data.get("target_type", "")
        if not target_type:
            continue
        bucket = out.setdefault(ann.file, set())
        bucket.add(target_type)
        # Resolve the simple type name to the module the import brought in.
        for module in imports_by_file.get(ann.file, frozenset()):
            if module.endswith("." + target_type) or module == target_type:
                bucket.add(module)
                bucket.add(module.split(".", 1)[0])
    return {file: frozenset(modules) for file, modules in out.items()}


