"""Python source -> Facts via tree-sitter.

Replacement for the stdlib-`ast`-based `PythonGrammar`. The design goal is
**complete extraction in one pass**: every observable structure in the file
becomes a Fact, so downstream resolvers never need to re-parse or extend
the grammar to handle new patterns. Adding a new language is then a new
tree-sitter grammar file, not a 270-line Python class.

Fact shapes emitted (in addition to what the legacy grammar emitted):

  SYMBOL (function/method) carries:
    name, sym_kind, file, line, line_end, enclosing_class, enclosing_fn,
    is_async, is_generator, visibility,
    params: [{name, type_hint, default_kind, kind}]
    return_type, decorators, references,
    self_assignments: [{attr, source_kind, source}]

  CLASS_DEF carries:
    name, file, line, line_end, bases, enclosing_class, decorators,
    is_abstract, generic_params,
    init_params: [{name, type_hint}],
    class_attrs: [{name, type_hint}]

  IMPORT carries: module, names, alias, level, imported_as

  CALL carries: callee, receiver, method, receiver_chain,
    args: [{kind, value}], kwargs: {name: {kind, value}}

  ASSIGNMENT (new): target, target_chain, source_kind, source, type_hint, scope

  DECORATOR (Python only — Java keeps ANNOTATION): callee, args, kwargs,
    target_symbol, target_line. Same shape as the legacy grammar so the
    resolver continues to consume it unchanged.

Failure mode: a malformed file makes the parser produce a tree with ERROR
nodes; we ignore those nodes and emit whatever else we found. The grammar
never raises.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.facts import Fact, FactKind
from ingestion.grammars.grammar import Grammar


_PARAM_KIND_BY_NODE_TYPE: dict[str, str] = {
    "identifier": "pos",
    "typed_parameter": "pos",
    "default_parameter": "pos",
    "typed_default_parameter": "pos",
    "list_splat_pattern": "var",
    "dictionary_splat_pattern": "kwvar",
    "keyword_separator": None,  # `*` marker, skip
    "positional_separator": None,  # `/` marker, skip
}


class TreeSitterPythonGrammar(Grammar):
    """Tree-sitter-driven extractor. Parser is built once per instance."""

    suffixes = (".py",)

    def __init__(self) -> None:
        import tree_sitter_python as tsp
        from tree_sitter import Language, Parser

        self._language = Language(tsp.language())
        self._parser = Parser(self._language)

    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        try:
            tree = self._parser.parse(bytes(content, "utf-8"))
        except Exception:
            return []
        if tree.root_node is None:
            return []
        ctx = _Ctx(file=str(file), repo_id=repo_id, source_bytes=bytes(content, "utf-8"))
        facts: list[Fact] = []
        self._walk_block(tree.root_node, facts, ctx, enclosing_class="", enclosing_fn="")
        return facts

    # ------------------------------------------------------------------
    # block walker: handles module body, function body, class body
    # ------------------------------------------------------------------

    def _walk_block(
        self,
        node: Any,
        facts: list[Fact],
        ctx: "_Ctx",
        *,
        enclosing_class: str,
        enclosing_fn: str,
    ) -> None:
        for child in node.named_children:
            t = child.type
            if t in ("import_statement", "import_from_statement"):
                self._emit_import(child, facts, ctx)
            elif t == "decorated_definition":
                self._handle_decorated(
                    child, facts, ctx,
                    enclosing_class=enclosing_class,
                    enclosing_fn=enclosing_fn,
                )
            elif t == "function_definition":
                self._emit_function(
                    child, facts, ctx,
                    enclosing_class=enclosing_class,
                    enclosing_fn=enclosing_fn,
                    decorators=(),
                )
            elif t == "class_definition":
                self._emit_class(
                    child, facts, ctx,
                    enclosing_class=enclosing_class,
                    decorators=(),
                )
            elif t in ("assignment",):
                self._emit_assignment(
                    child, facts, ctx,
                    scope=_scope_of(enclosing_class, enclosing_fn),
                )
                for call in _iter_calls(child):
                    self._emit_call(call, facts, ctx)
            elif t == "expression_statement":
                # tree-sitter wraps top-level statements in expression_statement;
                # an `x = …` lives as expression_statement > assignment. Descend
                # to surface the assignment and any nested calls.
                scope = _scope_of(enclosing_class, enclosing_fn)
                for inner in child.named_children:
                    if inner.type == "assignment":
                        self._emit_assignment(inner, facts, ctx, scope=scope)
                for call in _iter_calls(child):
                    self._emit_call(call, facts, ctx)
            elif t in ("if_statement", "try_statement", "with_statement", "for_statement", "while_statement"):
                # Recurse into compound statements without changing scope.
                self._walk_block(
                    child, facts, ctx,
                    enclosing_class=enclosing_class,
                    enclosing_fn=enclosing_fn,
                )
            elif t == "block":
                self._walk_block(
                    child, facts, ctx,
                    enclosing_class=enclosing_class,
                    enclosing_fn=enclosing_fn,
                )
            else:
                # CALLs and assignments inside any other compound (e.g. match)
                for call in _iter_calls(child):
                    self._emit_call(call, facts, ctx)

    # ------------------------------------------------------------------
    # imports
    # ------------------------------------------------------------------

    def _emit_import(self, node: Any, facts: list[Fact], ctx: "_Ctx") -> None:
        line = node.start_point[0] + 1
        if node.type == "import_statement":
            # `import a, b as c, d.e`
            for spec in node.named_children:
                if spec.type == "aliased_import":
                    mod_node = spec.child_by_field_name("name")
                    alias_node = spec.child_by_field_name("alias")
                    module = _text(mod_node, ctx) if mod_node else ""
                    alias = _text(alias_node, ctx) if alias_node else ""
                else:
                    module = _text(spec, ctx)
                    alias = ""
                facts.append(
                    Fact(
                        kind=FactKind.IMPORT,
                        file=ctx.file,
                        line=line,
                        repo_id=ctx.repo_id,
                        data={
                            "module": module,
                            "names": [],
                            "alias": alias,
                            "level": 0,
                            "imported_as": {},
                        },
                    )
                )
            return
        # import_from_statement: `from MOD import a, b as c` or `from .X import …`
        module_node = node.child_by_field_name("module_name")
        module = _text(module_node, ctx) if module_node else ""
        # Relative dots: leading "." characters appear as anonymous children
        # before the module name. Count them.
        level = 0
        for c in node.children:
            if c.type == "import_prefix":
                level = len(_text(c, ctx))
                break
            if c.type == "from":
                continue
        if module.startswith("."):
            # Some grammars surface the dots as part of the module_name.
            stripped = module.lstrip(".")
            level = len(module) - len(stripped)
            module = stripped
        names: list[str] = []
        imported_as: dict[str, str] = {}
        for c in node.children_by_field_name("name"):
            if c.type == "aliased_import":
                name_node = c.child_by_field_name("name")
                alias_node = c.child_by_field_name("alias")
                name = _text(name_node, ctx) if name_node else ""
                alias = _text(alias_node, ctx) if alias_node else ""
                names.append(name)
                if alias:
                    imported_as[name] = alias
            else:
                names.append(_text(c, ctx))
        facts.append(
            Fact(
                kind=FactKind.IMPORT,
                file=ctx.file,
                line=line,
                repo_id=ctx.repo_id,
                data={
                    "module": module,
                    "names": names,
                    "alias": "",
                    "level": level,
                    "imported_as": imported_as,
                },
            )
        )

    # ------------------------------------------------------------------
    # decorated function / class
    # ------------------------------------------------------------------

    def _handle_decorated(
        self,
        node: Any,
        facts: list[Fact],
        ctx: "_Ctx",
        *,
        enclosing_class: str,
        enclosing_fn: str,
    ) -> None:
        decorators_raw: list[Any] = [c for c in node.named_children if c.type == "decorator"]
        # The actual definition is the last named child (function or class).
        inner = node.named_children[-1] if node.named_children else None
        if inner is None:
            return
        if inner.type == "function_definition":
            self._emit_function(
                inner, facts, ctx,
                enclosing_class=enclosing_class,
                enclosing_fn=enclosing_fn,
                decorators=tuple(decorators_raw),
            )
        elif inner.type == "class_definition":
            self._emit_class(
                inner, facts, ctx,
                enclosing_class=enclosing_class,
                decorators=tuple(decorators_raw),
            )

    # ------------------------------------------------------------------
    # function / method
    # ------------------------------------------------------------------

    def _emit_function(
        self,
        node: Any,
        facts: list[Fact],
        ctx: "_Ctx",
        *,
        enclosing_class: str,
        enclosing_fn: str,
        decorators: tuple[Any, ...],
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(name_node, ctx)
        line = node.start_point[0] + 1
        line_end = node.end_point[0] + 1
        sym_kind = "method" if enclosing_class else "function"
        is_async = any(c.type == "async" for c in node.children)
        params_node = node.child_by_field_name("parameters")
        params = _collect_params(params_node, ctx) if params_node else []
        return_node = node.child_by_field_name("return_type")
        return_type = _qualified_text(return_node, ctx) if return_node else ""
        body = node.child_by_field_name("body")
        references: list[str] = sorted(_collect_references(body, ctx)) if body else []
        is_generator = _has_yield(body) if body else False
        self_assignments = _collect_self_assignments(body, ctx) if body else []
        # Convert params from tuples to dicts; back-compat: also keep the
        # legacy `params: [[name, type]]` shape so today's resolver continues
        # to read them without code changes.
        params_rich = [
            {
                "name": p[0],
                "type_hint": p[1],
                "default_kind": p[2],
                "kind": p[3],
            }
            for p in params
        ]
        params_legacy = [(p[0], p[1]) for p in params]
        decorator_callees = [_decorator_callee(d, ctx) for d in decorators]
        visibility = "private" if name.startswith("_") and not name.startswith("__") else "public"
        facts.append(
            Fact(
                kind=FactKind.SYMBOL,
                file=ctx.file,
                line=line,
                line_end=line_end,
                repo_id=ctx.repo_id,
                data={
                    "sym_kind": sym_kind,
                    "name": name,
                    "is_async": is_async,
                    "is_generator": is_generator,
                    "enclosing_class": enclosing_class,
                    "enclosing_fn": enclosing_fn,
                    "visibility": visibility,
                    "references": references,
                    "params": params_legacy,
                    "params_rich": params_rich,
                    "return_type": return_type,
                    "decorators_applied": [c for c in decorator_callees if c],
                    "self_assignments": self_assignments,
                },
            )
        )
        # Emit per-decorator DECORATOR facts (resolver consumes these).
        for dec in decorators:
            facts.append(_decorator_fact(dec, ctx, target_name=name, target_line=line))
        # Default values of parameters (`= Depends(get_db)`) live in the
        # signature, not the body — so the body walker would miss them.
        # Emit CALL facts for any call appearing as a default value so the
        # Depends(...) pattern is observable downstream.
        if params_node is not None:
            for call in _iter_calls(params_node):
                self._emit_call(call, facts, ctx)
        # Recurse into the body so we capture: nested defs, CALL facts,
        # and inner-scope ASSIGNMENTs (we only retain self.X and module-level).
        if body is not None:
            self._walk_block(
                body, facts, ctx,
                enclosing_class=enclosing_class,
                enclosing_fn=name,
            )

    # ------------------------------------------------------------------
    # class
    # ------------------------------------------------------------------

    def _emit_class(
        self,
        node: Any,
        facts: list[Fact],
        ctx: "_Ctx",
        *,
        enclosing_class: str,
        decorators: tuple[Any, ...],
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(name_node, ctx)
        line = node.start_point[0] + 1
        line_end = node.end_point[0] + 1
        # Bases (superclasses) — `argument_list` containing identifiers,
        # attribute accesses, or sometimes `Generic[T]`.
        bases: list[str] = []
        generic_params: list[str] = []
        supers = node.child_by_field_name("superclasses")
        if supers is not None:
            for c in supers.named_children:
                if c.type == "subscript":
                    head = c.child_by_field_name("value")
                    if head is not None and _text(head, ctx) in ("Generic", "Protocol"):
                        for sub in c.children_by_field_name("subscript"):
                            generic_params.append(_text(sub, ctx))
                bases.append(_qualified_text(c, ctx))
        decorator_callees = [_decorator_callee(d, ctx) for d in decorators]
        is_abstract = any("ABC" in b or "abstract" in b.lower() for b in bases) or any(
            "abstract" in cc.lower() for cc in decorator_callees
        )
        body = node.child_by_field_name("body")
        init_params: list[tuple[str, str]] = []
        class_attrs: list[tuple[str, str]] = []
        if body is not None:
            init_params, class_attrs = _scan_class_body_for_summary(body, ctx)
        facts.append(
            Fact(
                kind=FactKind.CLASS_DEF,
                file=ctx.file,
                line=line,
                line_end=line_end,
                repo_id=ctx.repo_id,
                data={
                    "name": name,
                    "bases": bases,
                    "enclosing_class": enclosing_class,
                    "decorators_applied": [c for c in decorator_callees if c],
                    "is_abstract": is_abstract,
                    "generic_params": generic_params,
                    "init_params": init_params,
                    "class_attrs": class_attrs,
                },
            )
        )
        for dec in decorators:
            facts.append(_decorator_fact(dec, ctx, target_name=name, target_line=line))
        if body is not None:
            self._walk_block(
                body, facts, ctx,
                enclosing_class=name,
                enclosing_fn="",
            )

    # ------------------------------------------------------------------
    # assignment
    # ------------------------------------------------------------------

    def _emit_assignment(
        self,
        node: Any,
        facts: list[Fact],
        ctx: "_Ctx",
        *,
        scope: str,
    ) -> None:
        # We only retain module-level and `self.X = ...` assignments; that
        # subset captures the type-inference signals downstream resolvers
        # need without blowing up the fact count on locals.
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        type_node = node.child_by_field_name("type")
        if left is None:
            return
        target_chain = _chain_of(left, ctx)
        if not target_chain:
            return
        is_self_attr = len(target_chain) >= 2 and target_chain[0] == "self"
        if scope == "module":
            pass  # keep
        elif scope == "method" and is_self_attr:
            pass  # keep
        else:
            return
        target = target_chain[-1]
        source_kind, source = _classify_source(right, ctx)
        type_hint = _qualified_text(type_node, ctx) if type_node else ""
        line = node.start_point[0] + 1
        facts.append(
            Fact(
                kind=FactKind.ASSIGNMENT,
                file=ctx.file,
                line=line,
                repo_id=ctx.repo_id,
                data={
                    "target": target,
                    "target_chain": target_chain,
                    "source_kind": source_kind,
                    "source": source,
                    "type_hint": type_hint,
                    "scope": scope,
                },
            )
        )

    # ------------------------------------------------------------------
    # call
    # ------------------------------------------------------------------

    def _emit_call(self, node: Any, facts: list[Fact], ctx: "_Ctx") -> None:
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        callee = _qualified_text(fn, ctx)
        if not callee:
            return
        chain = _chain_of(fn, ctx)
        receiver, _, method = callee.rpartition(".")
        args_node = node.child_by_field_name("arguments")
        positional: list[Any] = []
        kwargs: dict[str, Any] = {}
        positional_rich: list[dict[str, Any]] = []
        kwargs_rich: dict[str, dict[str, Any]] = {}
        if args_node is not None:
            for arg in args_node.named_children:
                if arg.type == "keyword_argument":
                    name_node = arg.child_by_field_name("name")
                    val_node = arg.child_by_field_name("value")
                    if name_node is None or val_node is None:
                        continue
                    kname = _text(name_node, ctx)
                    val, kind = _literal_value_and_kind(val_node, ctx)
                    kwargs[kname] = val
                    kwargs_rich[kname] = {"kind": kind, "value": val}
                else:
                    val, kind = _literal_value_and_kind(arg, ctx)
                    positional.append(val)
                    positional_rich.append({"kind": kind, "value": val})
        line = node.start_point[0] + 1
        facts.append(
            Fact(
                kind=FactKind.CALL,
                file=ctx.file,
                line=line,
                repo_id=ctx.repo_id,
                data={
                    "callee": callee,
                    "receiver": receiver,
                    "method": method,
                    "receiver_chain": chain[:-1],  # everything before the called name
                    "args": positional,
                    "kwargs": kwargs,
                    "args_rich": positional_rich,
                    "kwargs_rich": kwargs_rich,
                },
            )
        )


# ============================================================================
# helpers
# ============================================================================


class _Ctx:
    __slots__ = ("file", "repo_id", "source_bytes")

    def __init__(self, file: str, repo_id: str, source_bytes: bytes) -> None:
        self.file = file
        self.repo_id = repo_id
        self.source_bytes = source_bytes


def _text(node: Any, ctx: _Ctx) -> str:
    if node is None:
        return ""
    return ctx.source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _qualified_text(node: Any, ctx: _Ctx) -> str:
    """Dotted-name text for an identifier or attribute. Returns "" for
    anything more complex than a name/attribute chain — matches the
    behavior of the legacy `_qualified_name` helper."""
    if node is None:
        return ""
    t = node.type
    if t == "identifier":
        return _text(node, ctx)
    if t in ("attribute", "dotted_name"):
        parts = _chain_of(node, ctx)
        return ".".join(parts) if parts else ""
    if t == "type":
        # `return_type` field wraps the actual type node.
        inner = node.named_children[0] if node.named_children else None
        return _qualified_text(inner, ctx) if inner else ""
    if t == "call":
        # `@foo.bar(...)` — return the qualified callee.
        fn = node.child_by_field_name("function")
        return _qualified_text(fn, ctx) if fn else ""
    if t == "subscript":
        # `list[Foo]`, `Optional[X]` — fall back to bare text.
        return _text(node, ctx)
    return ""


def _chain_of(node: Any, ctx: _Ctx) -> list[str]:
    """Decompose an attribute/identifier access into its segments.

    `self.repo.users` -> ["self", "repo", "users"]
    `foo`             -> ["foo"]
    Other shapes      -> []
    """
    if node is None:
        return []
    if node.type == "identifier":
        return [_text(node, ctx)]
    if node.type == "attribute":
        obj = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute")
        head = _chain_of(obj, ctx)
        if head and attr is not None:
            return head + [_text(attr, ctx)]
    if node.type == "dotted_name":
        return _text(node, ctx).split(".")
    return []


def _has_yield(body: Any) -> bool:
    """Walk the function body looking for a yield expression. Used to
    distinguish generators — matters for the `is_generator` flag downstream
    typing/reachability analyses would want."""
    if body is None:
        return False
    stack = [body]
    while stack:
        n = stack.pop()
        if n.type in ("yield", "yield_expression"):
            return True
        # Don't descend into nested function/class scopes — yields there
        # belong to inner generators, not this one.
        if n.type in ("function_definition", "class_definition", "lambda"):
            continue
        stack.extend(n.children)
    return False


def _collect_references(body: Any, ctx: _Ctx) -> set[str]:
    """Names referenced in the function body. Used by CoverageResolver to
    distinguish "imported" from "actually touched"."""
    refs: set[str] = set()
    if body is None:
        return refs
    stack = [body]
    while stack:
        n = stack.pop()
        if n.type == "identifier":
            refs.add(_text(n, ctx))
            continue
        # Don't descend into nested defs — their refs belong to them.
        if n.type in ("function_definition", "class_definition"):
            continue
        stack.extend(n.children)
    return refs


def _collect_self_assignments(body: Any, ctx: _Ctx) -> list[dict[str, str]]:
    """`self.X = …` statements inside this function body. We capture the
    attr name and the RHS's source kind/value so the resolver can later
    answer "what type is `self.X`?" without re-parsing.
    """
    out: list[dict[str, str]] = []
    if body is None:
        return out
    stack = list(body.children)
    while stack:
        n = stack.pop()
        if n.type == "assignment":
            left = n.child_by_field_name("left")
            right = n.child_by_field_name("right")
            type_node = n.child_by_field_name("type")
            if left is None or right is None:
                continue
            chain = _chain_of(left, ctx)
            if len(chain) < 2 or chain[0] != "self":
                continue
            source_kind, source = _classify_source(right, ctx)
            type_hint = _qualified_text(type_node, ctx) if type_node else ""
            out.append(
                {
                    "attr": chain[-1],
                    "source_kind": source_kind,
                    "source": source,
                    "type_hint": type_hint,
                }
            )
            continue
        if n.type in ("function_definition", "class_definition"):
            continue
        stack.extend(n.children)
    return out


def _classify_source(node: Any, ctx: _Ctx) -> tuple[str, str]:
    """Return (kind, source) for an assignment RHS.

    kind ∈ {call, name, attr, literal, expr}; source is a stringified
    representation specific to each kind (callee name for `call`, bare
    identifier for `name`, dotted path for `attr`, literal value for
    `literal`, "" for `expr`).
    """
    if node is None:
        return "expr", ""
    t = node.type
    if t == "call":
        fn = node.child_by_field_name("function")
        return "call", _qualified_text(fn, ctx) if fn else ""
    if t == "identifier":
        return "name", _text(node, ctx)
    if t == "attribute":
        return "attr", ".".join(_chain_of(node, ctx))
    if t in ("string", "integer", "float", "true", "false", "none"):
        return "literal", _text(node, ctx)
    return "expr", ""


def _literal_value_and_kind(node: Any, ctx: _Ctx) -> tuple[Any, str]:
    """Mirror the legacy `_literal_value` placeholder protocol used by the
    AST grammar, AND tag the kind so the rich `args_rich` payload can keep
    type info downstream. Resolvers consuming the old `args` list see the
    same `<name:foo>` / `<attr:a.b>` placeholders as before.
    """
    t = node.type
    if t == "string":
        # tree-sitter wraps the contents in quotes; strip them.
        raw = _text(node, ctx)
        if len(raw) >= 2 and raw[0] in ("\"", "'") and raw[-1] == raw[0]:
            raw = raw[1:-1]
        elif raw.startswith(("f", "r", "b", "u")) and len(raw) >= 3:
            # f-strings / r-strings: drop the prefix + outer quotes.
            inner = raw.lstrip("frbuFRBU")
            if len(inner) >= 2 and inner[0] == inner[-1]:
                raw = inner[1:-1]
        return raw, "literal"
    if t == "integer":
        try:
            return int(_text(node, ctx)), "literal"
        except ValueError:
            return _text(node, ctx), "literal"
    if t == "float":
        try:
            return float(_text(node, ctx)), "literal"
        except ValueError:
            return _text(node, ctx), "literal"
    if t == "true":
        return True, "literal"
    if t == "false":
        return False, "literal"
    if t == "none":
        return None, "literal"
    if t == "identifier":
        return f"<name:{_text(node, ctx)}>", "name"
    if t == "attribute":
        qn = ".".join(_chain_of(node, ctx))
        return (f"<attr:{qn}>" if qn else "<expr>"), "attr"
    if t == "call":
        fn = node.child_by_field_name("function")
        return (f"<call:{_qualified_text(fn, ctx) or '?'}>"), "call"
    if t in ("list", "tuple"):
        return [_literal_value_and_kind(c, ctx)[0] for c in node.named_children], "expr"
    if t == "dictionary":
        out: dict[Any, Any] = {}
        for pair in node.named_children:
            if pair.type != "pair":
                continue
            k = pair.child_by_field_name("key")
            v = pair.child_by_field_name("value")
            k_val = _literal_value_and_kind(k, ctx)[0] if k else "<expr>"
            v_val = _literal_value_and_kind(v, ctx)[0] if v else "<expr>"
            out[k_val] = v_val
        return out, "expr"
    return "<expr>", "expr"


def _collect_params(params_node: Any, ctx: _Ctx) -> list[tuple[str, str, str, str]]:
    """Return tuples (name, type_hint, default_kind, kind).

    `default_kind` ∈ {"", "default"} — whether the param has a default.
    `kind` ∈ {self, cls, pos, kw, var, kwvar}.
    """
    out: list[tuple[str, str, str, str]] = []
    saw_star = False
    for p in params_node.named_children:
        pt = p.type
        if pt == "keyword_separator":
            saw_star = True
            continue
        if pt == "positional_separator":
            continue
        if pt == "list_splat_pattern":
            inner = p.named_children[0] if p.named_children else None
            name = _text(inner, ctx) if inner else "args"
            out.append((name, "", "", "var"))
            saw_star = True
            continue
        if pt == "dictionary_splat_pattern":
            inner = p.named_children[0] if p.named_children else None
            name = _text(inner, ctx) if inner else "kwargs"
            out.append((name, "", "", "kwvar"))
            continue
        kind_label = "kw" if saw_star else "pos"
        if pt == "identifier":
            name = _text(p, ctx)
            type_hint = ""
            default_kind = ""
        elif pt == "typed_parameter":
            ident = next((c for c in p.children if c.type == "identifier"), None)
            ty = p.child_by_field_name("type")
            name = _text(ident, ctx) if ident else ""
            type_hint = _qualified_text(ty, ctx) if ty else ""
            default_kind = ""
        elif pt == "default_parameter":
            name_node = p.child_by_field_name("name")
            name = _text(name_node, ctx) if name_node else ""
            type_hint = ""
            default_kind = "default"
        elif pt == "typed_default_parameter":
            name_node = p.child_by_field_name("name")
            ty = p.child_by_field_name("type")
            name = _text(name_node, ctx) if name_node else ""
            type_hint = _qualified_text(ty, ctx) if ty else ""
            default_kind = "default"
        else:
            continue
        # Promote self/cls into their own kinds — the resolver uses these.
        if name == "self":
            kind_label = "self"
        elif name == "cls":
            kind_label = "cls"
        out.append((name, type_hint, default_kind, kind_label))
    return out


def _scan_class_body_for_summary(
    body: Any, ctx: _Ctx
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (init_params, class_attrs) for use by resolvers.

    init_params is the parameter list of any `__init__` method (without
    self). class_attrs is the list of class-level annotated assignments
    like `count: int = 0`. Both shapes mirror what the resolver expects.
    """
    init_params: list[tuple[str, str]] = []
    class_attrs: list[tuple[str, str]] = []
    for child in body.named_children:
        node = child
        if child.type == "decorated_definition":
            inner = child.named_children[-1] if child.named_children else None
            node = inner if inner is not None else child
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None and _text(name_node, ctx) == "__init__":
                params = node.child_by_field_name("parameters")
                if params is not None:
                    for entry in _collect_params(params, ctx):
                        if entry[3] in ("self", "cls"):
                            continue
                        init_params.append((entry[0], entry[1]))
        elif node.type == "expression_statement":
            # Class-level annotated assignment: `count: int = 0` or `count: int`
            inner = node.named_children[0] if node.named_children else None
            if inner is None:
                continue
            if inner.type == "assignment":
                left = inner.child_by_field_name("left")
                ty = inner.child_by_field_name("type")
                if left is not None and ty is not None and left.type == "identifier":
                    class_attrs.append((_text(left, ctx), _qualified_text(ty, ctx)))
    return init_params, class_attrs


def _decorator_callee(dec_node: Any, ctx: _Ctx) -> str:
    """Return the callee text of a decorator (no args)."""
    # decorator wraps either an attribute/identifier or a `call` whose
    # function is the decorator name.
    for c in dec_node.named_children:
        if c.type == "call":
            fn = c.child_by_field_name("function")
            return _qualified_text(fn, ctx) if fn else ""
        if c.type in ("identifier", "attribute", "dotted_name"):
            return _qualified_text(c, ctx)
    return ""


def _decorator_fact(
    dec_node: Any, ctx: _Ctx, *, target_name: str, target_line: int
) -> Fact:
    """Per-decorator DECORATOR fact in the same shape the legacy grammar
    emitted — so resolvers consume it unchanged."""
    callee = ""
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for c in dec_node.named_children:
        if c.type == "call":
            fn = c.child_by_field_name("function")
            callee = _qualified_text(fn, ctx) if fn else ""
            args_node = c.child_by_field_name("arguments")
            if args_node is not None:
                for arg in args_node.named_children:
                    if arg.type == "keyword_argument":
                        name_node = arg.child_by_field_name("name")
                        val_node = arg.child_by_field_name("value")
                        if name_node and val_node:
                            kwargs[_text(name_node, ctx)] = _literal_value_and_kind(val_node, ctx)[0]
                    else:
                        args.append(_literal_value_and_kind(arg, ctx)[0])
        elif c.type in ("identifier", "attribute", "dotted_name") and not callee:
            callee = _qualified_text(c, ctx)
    return Fact(
        kind=FactKind.DECORATOR,
        file=ctx.file,
        line=dec_node.start_point[0] + 1,
        repo_id=ctx.repo_id,
        data={
            "callee": callee,
            "args": args,
            "kwargs": kwargs,
            "target_symbol": target_name,
            "target_line": target_line,
        },
    )


def _iter_calls(node: Any):
    """Yield every `call` node descended from `node`, skipping nested
    function/class definitions (those run when *their* body walker fires).
    """
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "call":
            yield n
            # Calls may contain nested calls in args — descend.
        if n.type in ("function_definition", "class_definition", "lambda", "decorated_definition"):
            continue
        stack.extend(n.children)


def _scope_of(enclosing_class: str, enclosing_fn: str) -> str:
    if enclosing_fn:
        return "method" if enclosing_class else "function"
    if enclosing_class:
        return "class"
    return "module"
