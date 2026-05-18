"""Python source -> Facts via the stdlib `ast` module.

Emits:
  * IMPORT — one per `import` / `from … import …`
  * SYMBOL — one per top-level def, async def, or method
  * CLASS_DEF — one per class declaration
  * DECORATOR — one per decorator, with callee + args + kwargs as primitives
  * CALL — module-level call expressions whose callee looks "interesting"
    (matches any framework's known patterns); cheap pre-filter is done here,
    semantic filtering happens in resolvers

No interpretation lives here — there are no `_TEST_ANNOTATIONS` or
`_EXTERNAL_HINTS` constants. Those live in `frameworks/*.yaml` and the
resolvers consult them.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from core.facts import Fact, FactKind
from ingestion.grammars.grammar import Grammar


class PythonGrammar(Grammar):
    suffixes = (".py",)

    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        try:
            tree = ast.parse(content, filename=str(file))
        except SyntaxError:
            return []

        facts: list[Fact] = []
        file_str = str(file)
        self._collect_imports(tree, file_str, repo_id, facts)
        self._collect_definitions(tree, file_str, repo_id, facts, enclosing_class=None)
        self._collect_module_calls(tree, file_str, repo_id, facts)
        return facts

    def _collect_imports(
        self, tree: ast.AST, file: str, repo_id: str, facts: list[Fact]
    ) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    facts.append(
                        Fact(
                            kind=FactKind.IMPORT,
                            file=file,
                            line=node.lineno,
                            repo_id=repo_id,
                            data={
                                "module": alias.name,
                                "names": [],
                                "alias": alias.asname or "",
                            },
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                facts.append(
                    Fact(
                        kind=FactKind.IMPORT,
                        file=file,
                        line=node.lineno,
                        repo_id=repo_id,
                        data={
                            "module": module,
                            "names": [alias.name for alias in node.names],
                            "alias": "",
                            "level": node.level,
                        },
                    )
                )

    def _collect_definitions(
        self,
        node: ast.AST,
        file: str,
        repo_id: str,
        facts: list[Fact],
        enclosing_class: str | None,
    ) -> None:
        """Recursive descent so that methods nested in classes carry their
        enclosing class name in `data.enclosing_class`."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                line_end = getattr(child, "end_lineno", child.lineno) or child.lineno
                facts.append(
                    Fact(
                        kind=FactKind.CLASS_DEF,
                        file=file,
                        line=child.lineno,
                        line_end=line_end,
                        repo_id=repo_id,
                        data={
                            "name": child.name,
                            "bases": [_qualified_name(b) for b in child.bases],
                            "enclosing_class": enclosing_class or "",
                        },
                    )
                )
                for dec in child.decorator_list:
                    facts.append(_decorator_fact(dec, file, child.lineno, repo_id, target_name=child.name))
                # recurse into class body for methods
                self._collect_definitions(child, file, repo_id, facts, enclosing_class=child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                line_end = getattr(child, "end_lineno", child.lineno) or child.lineno
                sym_kind = "method" if enclosing_class else "function"
                # Names referenced inside the body (`ast.Name` with Load ctx).
                # Used by CoverageResolver to distinguish "imported" from
                # "actually touched by this function" — kills file-scoped
                # over-coverage. Bounded and cheap: typically < 50 per fn.
                references = sorted(_collect_referenced_names(child))
                # Param (name, type-hint) pairs. FunctionCallResolver uses
                # these to resolve `param.method()` calls where `param` is
                # a typed argument (the FastAPI Depends pattern).
                params = _collect_params(child)
                facts.append(
                    Fact(
                        kind=FactKind.SYMBOL,
                        file=file,
                        line=child.lineno,
                        line_end=line_end,
                        repo_id=repo_id,
                        data={
                            "sym_kind": sym_kind,
                            "name": child.name,
                            "is_async": isinstance(child, ast.AsyncFunctionDef),
                            "enclosing_class": enclosing_class or "",
                            "references": references,
                            "params": params,
                        },
                    )
                )
                for dec in child.decorator_list:
                    facts.append(
                        _decorator_fact(dec, file, child.lineno, repo_id, target_name=child.name)
                    )
                # recurse for nested defs
                self._collect_definitions(child, file, repo_id, facts, enclosing_class=enclosing_class)
            else:
                # walk into non-def children (`if __name__ == "__main__":` etc.)
                self._collect_definitions(child, file, repo_id, facts, enclosing_class=enclosing_class)

    def _collect_module_calls(
        self, tree: ast.AST, file: str, repo_id: str, facts: list[Fact]
    ) -> None:
        """Capture any call expression as a CALL fact.

        We don't pre-filter by callee here — the resolver does that with
        framework YAML. The cost is bounded: a typical module has dozens of
        calls, not thousands.
        """
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = _qualified_name(node.func)
            if not callee:
                continue
            receiver, _, method = callee.rpartition(".")
            facts.append(
                Fact(
                    kind=FactKind.CALL,
                    file=file,
                    line=node.lineno,
                    repo_id=repo_id,
                    data={
                        "callee": callee,
                        "receiver": receiver,
                        "method": method,
                        "args": [_literal_value(a) for a in node.args],
                        "kwargs": {
                            kw.arg: _literal_value(kw.value)
                            for kw in node.keywords
                            if kw.arg is not None
                        },
                    },
                )
            )


def _collect_params(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[str, str]]:
    """Return (param_name, type_hint) tuples for every positional and
    keyword-only argument of `func`.

    `type_hint` is the dotted name of the annotation (`Session`,
    `app.services.UserService`) when one is present, otherwise "". Complex
    annotations the grammar can't unwrap to a name (`Optional[X]`,
    `list[Foo]`, callables) come back as "" — the resolver treats those
    as "no usable type". Star-args and `**kwargs` are skipped.
    """
    out: list[tuple[str, str]] = []
    args = func.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        type_hint = _qualified_name(arg.annotation) if arg.annotation is not None else ""
        out.append((arg.arg, type_hint))
    return out


def _collect_referenced_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return every `ast.Name` identifier used in Load context inside `func`'s
    body (and any nested defs). Used by CoverageResolver to tell whether a
    test actually touches a name it imported.

    We skip arguments and the function's own name (those are bindings, not
    references). We include attribute *receivers* (`foo` in `foo.bar`) and
    direct calls (`foo(...)`) since both signal that the imported symbol is
    touched.
    """
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
    return names


def _decorator_fact(
    decorator: ast.expr, file: str, target_line: int, repo_id: str, target_name: str
) -> Fact:
    """Build a DECORATOR fact from an `ast.expr` decorator node.

    The decorator's own line is `decorator.lineno` (one of the @-lines above
    the def). `target_line` is the def itself, so resolvers know which symbol
    this decorator wraps without a separate lookup.
    """
    callee = _qualified_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    if isinstance(decorator, ast.Call):
        args = [_literal_value(a) for a in decorator.args]
        kwargs = {kw.arg: _literal_value(kw.value) for kw in decorator.keywords if kw.arg is not None}
    return Fact(
        kind=FactKind.DECORATOR,
        file=file,
        line=getattr(decorator, "lineno", target_line),
        repo_id=repo_id,
        data={
            "callee": callee,
            "args": args,
            "kwargs": kwargs,
            "target_symbol": target_name,
            "target_line": target_line,
        },
    )


def _qualified_name(node: ast.expr | None) -> str:
    """`foo.bar.baz` for `Attribute(Attribute(Name('foo'), 'bar'), 'baz')`,
    `foo` for `Name('foo')`. Returns "" for anything more complex."""
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
        return ""
    if isinstance(node, ast.Call):
        return _qualified_name(node.func)
    return ""


def _literal_value(node: ast.expr) -> Any:
    """Convert an AST expression to a primitive when it's a literal;
    otherwise return a `<name:foo>` or `<expr>` placeholder so downstream
    code can still tell what the argument shape was."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return f"<name:{node.id}>"
    if isinstance(node, ast.Attribute):
        qn = _qualified_name(node)
        return f"<attr:{qn}>" if qn else "<expr>"
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal_value(elt) for elt in node.elts]
    if isinstance(node, ast.Dict):
        return {
            _literal_value(k) if k is not None else "<expr>": _literal_value(v)
            for k, v in zip(node.keys, node.values)
        }
    if isinstance(node, ast.Call):
        return f"<call:{_qualified_name(node.func) or '?'}>"
    return "<expr>"
