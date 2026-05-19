"""PythonDecoratorEndpointStrategy — covers FastAPI / Flask / Starlette / Litestar.

These frameworks share the same shape: a router/app instance whose method
decorators register routes (`@router.get("/x")`, `@app.route("/x")`),
optionally composed with mount calls (`app.include_router(router, prefix=...)`,
`app.register_blueprint(bp, url_prefix=...)`) and a base path declared at
app construction (`FastAPI(root_path="/v1")`).

Anything Spring-style (annotation-on-method with class-level prefix) lives
in `core/languages/java/extractors/endpoints/annotation.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.facts import Fact, FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import RoutePatterns
from core.languages.profile import (
    Grammar,
    GrammarKind,
    LanguageProfile,
    ModuleResolution,
    PackageAggregator,
)
from core.languages.resolution import resolve_candidate_files
from core.resolvers.endpoints.strategy import EndpointStrategy, register
from core.resolvers.endpoints.types import ResolvedEndpoint

_FALLBACK_PYTHON = LanguageProfile(
    name="python",
    file_extensions=(".py",),
    grammar=Grammar(
        kind=GrammarKind.NATIVE,
        driver="core.languages.python.grammar.PythonGrammar",
    ),
    module_resolution=ModuleResolution(
        separator=".",
        candidate_path_templates=("{module}.py", "{module}/__init__.py"),
    ),
    package_aggregator=PackageAggregator(files=("__init__.py",)),
)


class PythonDecoratorEndpointStrategy(EndpointStrategy):
    def resolve(
        self, *, tree: FactTree, fw: EffectiveFramework, repo_id: str
    ) -> list[ResolvedEndpoint]:
        routes = fw.routes
        if not isinstance(routes, RoutePatterns):
            return []
        base_path, base_derivation = _base_path(tree, routes)
        # Build a graph of routers so chained include_router calls across
        # files compose correctly. The old single-hop _mount_prefixes path
        # is kept as a fallback for the single-file pattern.
        router_graph = _build_router_graph(tree, routes)
        mount_prefixes = _mount_prefixes(tree, routes)

        results: list[ResolvedEndpoint] = []
        for dec in tree.where(kind=FactKind.DECORATOR):
            callee = str(dec.data.get("callee", ""))
            if not _matches_decorator_pattern(callee, routes.decorator_callee_patterns):
                continue
            method = _decorator_method(callee, routes)
            if not method:
                continue
            args = dec.data.get("args") or []
            path = next((a for a in args if isinstance(a, str) and a.startswith("/")), None)
            if path is None:
                continue
            receiver = callee.rsplit(".", 1)[0]
            # New: walk the router graph for a full multi-hop prefix.
            chain_prefix, chain_derivation = router_graph.resolve_prefix(
                dec.file, receiver
            )
            if not chain_prefix and not chain_derivation:
                # Fall back to the single-hop mount logic for codebases the
                # graph couldn't reason about (e.g. tests that don't model a
                # full router tree).
                chain_prefix, chain_derivation = _pick_mount(
                    mount_prefixes, receiver, dec.file
                )
            full = _join_paths(base_path, chain_prefix, path)
            derivation = (*base_derivation, *chain_derivation, dec.id)

            handler_symbol = tree.symbol_at(file=dec.file, line_after=dec.line)
            handler_name = (
                str(handler_symbol.data.get("name", "")) if handler_symbol is not None else ""
            )
            handler_file = handler_symbol.file if handler_symbol is not None else dec.file
            if handler_symbol is not None:
                derivation = (*derivation, handler_symbol.id)

            results.append(
                ResolvedEndpoint(
                    method=method.upper(),
                    full_path=full,
                    handler_file=handler_file,
                    handler_symbol=handler_name,
                    framework=fw.name,
                    derivation=derivation,
                )
            )
        return results


# ---- helpers --------------------------------------------------------------


def _base_path(tree: FactTree, routes: RoutePatterns) -> tuple[str, tuple[str, ...]]:
    for source in routes.base_path_sources:
        if source.callee is None:
            continue
        for call in tree.where(kind=FactKind.CALL):
            if call.data.get("callee") != source.callee:
                continue
            kwargs = call.data.get("kwargs") or {}
            value = kwargs.get(source.kwarg)
            if isinstance(value, str) and value.startswith("/"):
                return value, (call.id,)
    return "", ()


@dataclass(frozen=True)
class _MountInfo:
    receiver: str
    """The variable name the decorator's receiver should match
    (e.g. `router` for `@router.get(...)`)."""

    module_hint: str
    """When the include call wrote `charges.router`, this is `charges` —
    the source module the router lives in. Empty when the include used a
    bare name (single-file pattern)."""

    prefix: str
    call_id: str


def _mount_prefixes(tree: FactTree, routes: RoutePatterns) -> list[_MountInfo]:
    """Build a list of mounts for later (receiver, file-stem) matching.

    Two real-world patterns we support:

    1. `app.include_router(router, prefix="/api")` in one file —
       `router_token` is a bare name (`<name:router>`). The decorator using
       this router is in the same file. We store `module_hint=""`.

    2. `app.include_router(charges.router, prefix="/payments")` in main.py,
       with `router = APIRouter()` and `@router.get(...)` in
       `routers/charges.py`. The attr access `charges.router` tells us the
       module name (`charges`) which matches the decorator file's stem.
       Without this disambiguation, two `router = APIRouter()` variables
       in two files would collide on the simple name `router`.
    """
    out: list[_MountInfo] = []
    for mount in routes.mount_calls:
        for call in tree.where(kind=FactKind.CALL):
            if call.data.get("method") != mount.method:
                continue
            args = call.data.get("args") or []
            kwargs = call.data.get("kwargs") or {}
            router_arg = mount.router_arg or 0
            if router_arg >= len(args):
                continue
            router_token = args[router_arg]
            if not isinstance(router_token, str):
                continue
            receiver, module_hint = _parse_router_token(router_token)
            if not receiver:
                continue
            prefix = ""
            if mount.prefix_kwarg and isinstance(kwargs.get(mount.prefix_kwarg), str):
                prefix = kwargs[mount.prefix_kwarg]
            elif mount.prefix_arg is not None and mount.prefix_arg < len(args):
                candidate = args[mount.prefix_arg]
                if isinstance(candidate, str) and candidate.startswith("/"):
                    prefix = candidate
            out.append(
                _MountInfo(
                    receiver=receiver,
                    module_hint=module_hint,
                    prefix=prefix,
                    call_id=call.id,
                )
            )
    return out


def _pick_mount(
    mounts: list[_MountInfo], receiver: str, decorator_file: str
) -> tuple[str, tuple[str, ...]]:
    """Preference order: (1) mount with `module_hint == file stem` and
    matching receiver; (2) mount with empty `module_hint` and matching
    receiver (single-file pattern); (3) no match → empty prefix."""
    if not mounts:
        return "", ()
    file_stem = Path(decorator_file).stem
    fallback: _MountInfo | None = None
    for mount in mounts:
        if mount.receiver != receiver:
            continue
        if mount.module_hint and mount.module_hint == file_stem:
            return mount.prefix, (mount.call_id,)
        if not mount.module_hint and fallback is None:
            fallback = mount
    if fallback is not None:
        return fallback.prefix, (fallback.call_id,)
    return "", ()


def _parse_router_token(token: str) -> tuple[str, str]:
    """Return (receiver_name, module_hint) for a router token from a CALL fact.

    `<name:router>`         → ("router", "")
    `<attr:charges.router>` → ("router", "charges")
    `<attr:a.b.router>`     → ("router", "a.b")        (rare; nested module)
    other shapes            → ("", "")
    """
    if token.startswith("<name:") and token.endswith(">"):
        return token[len("<name:") : -1], ""
    if token.startswith("<attr:") and token.endswith(">"):
        path = token[len("<attr:") : -1]
        head, _, tail = path.rpartition(".")
        return tail, head
    return "", ""


def _decorator_method(callee: str, routes: RoutePatterns) -> str:
    """`router.get` -> `get`; respects the framework's declared methods."""
    method = callee.rsplit(".", 1)[-1]
    if routes.decorator_methods and method not in routes.decorator_methods:
        return ""
    return method


def _matches_decorator_pattern(callee: str, patterns: tuple[str, ...]) -> bool:
    """`router.get` matches `{any}.get`."""
    if not callee:
        return False
    for pattern in patterns:
        if pattern == callee:
            return True
        if "{any}" in pattern:
            literal = pattern.replace("{any}.", "")
            if callee.endswith("." + literal):
                return True
    return False


def _join_paths(*parts: str) -> str:
    joined: list[str] = []
    for raw in parts:
        if not raw:
            continue
        joined.append(raw.strip("/"))
    return "/" + "/".join(p for p in joined if p)


# ---- multi-hop router graph -----------------------------------------------


@dataclass(frozen=True)
class _IncludeEdge:
    """A `parent.include_router(child, prefix=…)` edge."""
    parent_file: str
    parent_var: str
    include_prefix: str
    call_id: str


@dataclass
class _RouterGraph:
    """Composable router structure across files.

    `own_prefix[(file, var)] = (prefix, call_id)` — the value passed to
        `APIRouter(prefix=…)` when this router was constructed.
    `parents[(child_file, child_var)] = _IncludeEdge` — who included this
        router and with what prefix.

    `resolve_prefix(file, var)` walks upward from `(file, var)` and
    concatenates the prefixes in the order FastAPI would, returning the
    full prefix string and the list of CALL fact ids that contributed —
    so endpoint derivation receipts trace every contributing call.
    """
    own_prefix: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    parents: dict[tuple[str, str], _IncludeEdge] = field(default_factory=dict)

    def resolve_prefix(
        self, file: str, var: str
    ) -> tuple[str, tuple[str, ...]]:
        prefix = ""
        derivation: list[str] = []
        visited: set[tuple[str, str]] = set()
        current = (file, var)
        while current not in visited:
            visited.add(current)
            own = self.own_prefix.get(current)
            if own is not None:
                own_prefix, own_id = own
                if own_prefix:
                    prefix = own_prefix + prefix
                    derivation.append(own_id)
            parent = self.parents.get(current)
            if parent is None:
                break
            if parent.include_prefix:
                prefix = parent.include_prefix + prefix
            derivation.append(parent.call_id)
            current = (parent.parent_file, parent.parent_var)
        return prefix, tuple(derivation)


def _build_router_graph(tree: FactTree, routes: RoutePatterns) -> _RouterGraph:
    """Construct the router graph for one FactTree.

    Two passes — first collect each `var = APIRouter(prefix=…)` to learn
    every router's own prefix, then collect each
    `parent.include_router(child, prefix=…)` to learn the parent-child
    edges. Child references are resolved to (source_file, source_var)
    via the caller file's IMPORT facts so cross-file chains compose.
    """
    graph = _RouterGraph()

    # 1) Own-prefix index. We need both the ASSIGNMENT (to know the var)
    # and the CALL (to read the prefix kwarg). They share file+line for
    # `var = APIRouter(...)`; correlate by (file, line).
    constructor_var_by_loc: dict[tuple[str, int], str] = {}
    for assign in tree.where(kind=FactKind.ASSIGNMENT):
        if assign.data.get("scope") != "module":
            continue
        if assign.data.get("source_kind") != "call":
            continue
        source = str(assign.data.get("source", ""))
        if not _is_apirouter_callee(source):
            continue
        chain = list(assign.data.get("target_chain") or ())
        if len(chain) != 1:
            continue
        constructor_var_by_loc[(assign.file, assign.line)] = chain[0]

    for call in tree.where(kind=FactKind.CALL):
        callee = str(call.data.get("callee", ""))
        if not _is_apirouter_callee(callee):
            continue
        var = constructor_var_by_loc.get((call.file, call.line))
        if not var:
            continue
        kwargs = call.data.get("kwargs") or {}
        prefix_val = kwargs.get("prefix")
        prefix = prefix_val if isinstance(prefix_val, str) else ""
        graph.own_prefix[(call.file, var)] = (prefix, call.id)

    # 2) Include-router edges.
    files_in_tree = tree.files()
    for mount in routes.mount_calls:
        if mount.method != "include_router":
            continue
        for call in tree.where(kind=FactKind.CALL):
            if call.data.get("method") != mount.method:
                continue
            args = call.data.get("args") or []
            kwargs = call.data.get("kwargs") or {}
            router_arg = mount.router_arg or 0
            if router_arg >= len(args):
                continue
            token = args[router_arg]
            if not isinstance(token, str):
                continue
            child = _resolve_child_router_ref(token, call.file, tree, files_in_tree)
            if child is None:
                continue
            receiver = str(call.data.get("receiver", ""))
            if not receiver:
                continue  # bare include_router; no parent to chain through

            prefix = ""
            if mount.prefix_kwarg and isinstance(kwargs.get(mount.prefix_kwarg), str):
                prefix = kwargs[mount.prefix_kwarg]
            elif mount.prefix_arg is not None and mount.prefix_arg < len(args):
                candidate = args[mount.prefix_arg]
                if isinstance(candidate, str) and candidate.startswith("/"):
                    prefix = candidate
            graph.parents[child] = _IncludeEdge(
                parent_file=call.file,
                parent_var=receiver,
                include_prefix=prefix,
                call_id=call.id,
            )
    return graph


def _is_apirouter_callee(callee: str) -> bool:
    """`APIRouter`, `fastapi.APIRouter`, `apis.APIRouter` — all match."""
    if not callee:
        return False
    return callee == "APIRouter" or callee.endswith(".APIRouter")


def _resolve_child_router_ref(
    token: str, caller_file: str, tree: FactTree, files_in_tree: list[str]
) -> tuple[str, str] | None:
    """Map an include_router token to its (source_file, var_name).

    Two token shapes (mirrors `_parse_router_token` but goes further):
      * `<name:user_router>` — local name; check caller's IMPORT facts
        for `from X.Y import router as user_router` to find the real
        (file, var). Fallback: same file.
      * `<attr:charges.router>` — attribute access; head (`charges`)
        is a module name imported into the caller. Resolve to the
        submodule's file and treat `router` as the var.
    """
    if token.startswith("<name:") and token.endswith(">"):
        name = token[len("<name:") : -1]
        for fact in _imports_in(tree, caller_file):
            module = str(fact.data.get("module", ""))
            names = list(fact.data.get("names") or [])
            imported_as = fact.data.get("imported_as") or {}
            if name in names and imported_as.get(name, name) == name:
                target_file = _resolve_module_to_file(module, files_in_tree)
                if target_file:
                    return target_file, name
            for orig_name, alias in imported_as.items():
                if alias == name:
                    target_file = _resolve_module_to_file(module, files_in_tree)
                    if target_file:
                        return target_file, orig_name
        # No import claims this name; assume it's a same-file variable.
        return caller_file, name

    if token.startswith("<attr:") and token.endswith(">"):
        path = token[len("<attr:") : -1]
        head, _, var = path.rpartition(".")
        if not head or not var:
            return None
        for fact in _imports_in(tree, caller_file):
            module = str(fact.data.get("module", ""))
            names = list(fact.data.get("names") or [])
            imported_as = fact.data.get("imported_as") or {}
            real_name = head
            if head in names and imported_as.get(head, head) == head:
                pass
            else:
                aliased = next(
                    (orig for orig, alias in imported_as.items() if alias == head),
                    None,
                )
                if aliased is None:
                    continue
                real_name = aliased
            effective = f"{module}.{real_name}" if module else real_name
            target_file = _resolve_module_to_file(effective, files_in_tree)
            if target_file:
                return target_file, var
    return None


def _imports_in(tree: FactTree, file: str) -> list[Fact]:
    return [f for f in tree.where(kind=FactKind.IMPORT) if f.file == file]


def _resolve_module_to_file(module: str, files_in_tree: list[str]) -> str | None:
    """Match a dotted module to an actual file in the tree by suffix.

    The IMPORT facts hold module names like `backend.app.admin.api.v1.sys.user`.
    `resolve_candidate_files` expands those to repo-relative templates
    using forward slashes (`backend/app/admin/api/v1/sys/user.py`). On
    Windows, `files_in_tree` may contain backslash-separated absolute
    paths — normalize both sides to forward slashes before suffix
    matching so the comparison is platform-agnostic.
    """
    if not module:
        return None
    for candidate in resolve_candidate_files(module, _FALLBACK_PYTHON):
        suffix = "/" + candidate
        for f in files_in_tree:
            norm = f.replace("\\", "/")
            if norm.endswith(suffix) or norm == candidate:
                return f
    return None


# Register for every Python decorator-driven framework we know.
register("fastapi", PythonDecoratorEndpointStrategy)
register("flask", PythonDecoratorEndpointStrategy)
