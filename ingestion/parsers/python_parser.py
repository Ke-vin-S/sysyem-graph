"""Python AST-based test extractor.

Identifies test functions (`def test_*` and methods on `TestCase`/`unittest`-
style classes), collects imports, detects mock-patching, and flags calls to
common HTTP/DB libraries that signal an integration test.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ingestion.parsers.parser import ParsedTest, Parser

# Libraries whose presence (when *not* mocked) implies the test touches real
# external systems. Used by the classifier downstream.
_EXTERNAL_HINTS: frozenset[str] = frozenset(
    {
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "urllib3",
        "psycopg2",
        "psycopg",
        "sqlalchemy",
        "redis",
        "boto3",
        "kafka",
        "confluent_kafka",
        "pymongo",
        "neo4j",
        "grpc",
    }
)

_MOCK_PATCHERS: frozenset[str] = frozenset(
    {"patch", "patch.object", "patch.multiple", "mock.patch", "MagicMock", "Mock"}
)


class PythonParser(Parser):
    suffixes = (".py",)

    def parse(self, file: Path, content: str) -> list[ParsedTest]:
        try:
            tree = ast.parse(content, filename=str(file))
        except SyntaxError:
            return []

        imports = _collect_imports(tree)
        external_imports = imports & _EXTERNAL_HINTS

        tests: list[ParsedTest] = []
        for node in _iter_test_defs(tree):
            decorators = tuple(_decorator_name(d) for d in node.decorator_list)
            mocked = _collect_mocked(node, decorators)
            calls_external = bool(external_imports - mocked) and _calls_attr_of(node, external_imports)
            tests.append(
                ParsedTest(
                    name=node.name,
                    file=str(file),
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    decorators=decorators,
                    imports=tuple(sorted(imports)),
                    mocked_modules=tuple(sorted(mocked)),
                    calls_external=calls_external,
                )
            )
        return tests


def _iter_test_defs(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_test_name(node.name):
            yield node


def _is_test_name(name: str) -> bool:
    return name.startswith("test_") or name == "test"


def _collect_imports(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".", 1)[0])
    return imports


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _collect_mocked(node: ast.FunctionDef | ast.AsyncFunctionDef, decorators: tuple[str, ...]) -> set[str]:
    mocked: set[str] = set()
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        name = _decorator_name(dec.func)
        if name not in _MOCK_PATCHERS:
            continue
        for arg in dec.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                mocked.add(arg.value.split(".", 1)[0])
    _ = decorators  # decorators are reported in ParsedTest separately
    # Walk body for inline `with patch(...)` blocks.
    for child in ast.walk(node):
        if isinstance(child, ast.With):
            for item in child.items:
                ce = item.context_expr
                if isinstance(ce, ast.Call):
                    name = _decorator_name(ce.func)
                    if name in _MOCK_PATCHERS:
                        for arg in ce.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                mocked.add(arg.value.split(".", 1)[0])
    return mocked


def _calls_attr_of(node: ast.AST, modules: frozenset[str] | set[str]) -> bool:
    """True iff the function body references `module.something` for any
    module in `modules` — a proxy for actually invoking the library."""
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            if child.value.id in modules:
                return True
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            if child.func.id in modules:
                return True
    return False
