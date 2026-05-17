"""Java source -> Facts via the pure-Python `javalang` parser.

Emits the same kinds as PythonGrammar (IMPORT, SYMBOL, CLASS_DEF, ANNOTATION,
CALL). Java has separate ANNOTATION facts to distinguish them from Python
decorators — they describe rather than wrap, so resolvers can match more
narrowly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.facts import Fact, FactKind
from ingestion.grammars.grammar import Grammar


class JavaGrammar(Grammar):
    suffixes = (".java",)

    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        try:
            import javalang
            from javalang.parser import JavaSyntaxError
        except ImportError:
            return []

        try:
            tree = javalang.parse.parse(content)
        except (JavaSyntaxError, Exception):
            return []

        facts: list[Fact] = []
        file_str = str(file)

        # Imports.
        for imp in tree.imports or []:
            facts.append(
                Fact(
                    kind=FactKind.IMPORT,
                    file=file_str,
                    line=_line_of(imp),
                    repo_id=repo_id,
                    data={
                        "module": imp.path,
                        "names": [],
                        "alias": "",
                        "static": bool(getattr(imp, "static", False)),
                        "wildcard": bool(getattr(imp, "wildcard", False)),
                    },
                )
            )

        # Classes and interfaces.
        for _, cls in tree.filter(javalang.tree.ClassDeclaration):
            facts.extend(_class_facts(cls, file_str, repo_id, javalang, kind_label="class"))
        for _, iface in tree.filter(javalang.tree.InterfaceDeclaration):
            facts.extend(_class_facts(iface, file_str, repo_id, javalang, kind_label="interface"))

        # Method declarations (including those inside annotations / interfaces).
        for _, method in tree.filter(javalang.tree.MethodDeclaration):
            facts.append(
                Fact(
                    kind=FactKind.SYMBOL,
                    file=file_str,
                    line=_line_of(method),
                    repo_id=repo_id,
                    data={
                        "sym_kind": "method",
                        "name": method.name,
                        "is_async": False,
                        "modifiers": sorted(method.modifiers or []),
                    },
                )
            )
            for ann in method.annotations or []:
                facts.append(_annotation_fact(ann, file_str, repo_id, target_name=method.name))

        # Field declarations: useful for @Mock fields.
        for _, field in tree.filter(javalang.tree.FieldDeclaration):
            type_name = getattr(getattr(field, "type", None), "name", None) or ""
            declarators = getattr(field, "declarators", None) or []
            for decl in declarators:
                facts.append(
                    Fact(
                        kind=FactKind.SYMBOL,
                        file=file_str,
                        line=_line_of(field),
                        repo_id=repo_id,
                        data={
                            "sym_kind": "field",
                            "name": getattr(decl, "name", ""),
                            "type": type_name,
                        },
                    )
                )
            for ann in field.annotations or []:
                # Attach annotation to the type, not just one declarator. The
                # resolver checks `target_type` to recognize @Mock OkHttpClient.
                facts.append(
                    _annotation_fact(
                        ann,
                        file_str,
                        repo_id,
                        target_name=type_name,
                        target_type=type_name,
                        target_kind="field",
                    )
                )

        # Method invocations — used as CALL facts for things like
        # `Mockito.mock(Foo.class)`.
        for _, call in tree.filter(javalang.tree.MethodInvocation):
            qualifier = getattr(call, "qualifier", "") or ""
            facts.append(
                Fact(
                    kind=FactKind.CALL,
                    file=file_str,
                    line=_line_of(call),
                    repo_id=repo_id,
                    data={
                        "callee": (f"{qualifier}.{call.member}" if qualifier else call.member),
                        "receiver": qualifier,
                        "method": call.member,
                        "args": [_arg_repr(arg) for arg in (call.arguments or [])],
                        "kwargs": {},
                    },
                )
            )

        return facts


def _class_facts(
    cls: object, file: str, repo_id: str, javalang_mod, kind_label: str
) -> list[Fact]:
    facts: list[Fact] = [
        Fact(
            kind=FactKind.CLASS_DEF,
            file=file,
            line=_line_of(cls),
            line_end=None,
            repo_id=repo_id,
            data={
                "name": getattr(cls, "name", ""),
                "kind": kind_label,
                "modifiers": sorted(getattr(cls, "modifiers", set()) or []),
            },
        )
    ]
    for ann in getattr(cls, "annotations", None) or []:
        facts.append(
            _annotation_fact(ann, file, repo_id, target_name=getattr(cls, "name", ""), target_kind="class")
        )
    return facts


def _annotation_fact(
    annotation: object,
    file: str,
    repo_id: str,
    target_name: str,
    target_type: str = "",
    target_kind: str = "method",
) -> Fact:
    name = getattr(annotation, "name", "") or ""
    # Strip qualifier if javalang gave us a fully-qualified annotation.
    simple = name.rsplit(".", 1)[-1]
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    element = getattr(annotation, "element", None)
    if element is not None:
        # element may be a single literal or a list of ElementValuePair.
        if isinstance(element, list):
            for pair in element:
                k = getattr(pair, "name", None)
                v = _arg_repr(getattr(pair, "value", None))
                if k:
                    kwargs[k] = v
                else:
                    args.append(v)
        else:
            args.append(_arg_repr(element))
    return Fact(
        kind=FactKind.ANNOTATION,
        file=file,
        line=_line_of(annotation),
        repo_id=repo_id,
        data={
            "callee": simple,
            "qualified": name,
            "args": args,
            "kwargs": kwargs,
            "target_symbol": target_name,
            "target_type": target_type,
            "target_kind": target_kind,
        },
    )


def _line_of(node: object) -> int:
    pos = getattr(node, "position", None)
    line = getattr(pos, "line", None) if pos is not None else None
    return int(line) if line else 1


def _arg_repr(value: object) -> Any:
    """Convert a javalang AST node to a JSON-safe primitive."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    # javalang Literal nodes carry a `.value` (always a string in javalang).
    lit_value = getattr(value, "value", None)
    if lit_value is not None and isinstance(lit_value, (str, int, float, bool)):
        return _strip_quotes(lit_value) if isinstance(lit_value, str) else lit_value
    # ClassReference / MemberReference / ReferenceType — surface the simple name.
    ref_type = getattr(value, "type", None)
    type_name = getattr(ref_type, "name", None)
    if type_name:
        return f"<class:{type_name}>"
    member = getattr(value, "member", None)
    if member:
        return f"<member:{member}>"
    name = getattr(value, "name", None)
    if name:
        return f"<name:{name}>"
    return "<expr>"


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value
