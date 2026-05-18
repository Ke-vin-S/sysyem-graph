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

from dataclasses import dataclass
from pathlib import Path

from core.facts import FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import RoutePatterns
from core.resolvers.endpoints.strategy import EndpointStrategy, register
from core.resolvers.endpoints.types import ResolvedEndpoint


class PythonDecoratorEndpointStrategy(EndpointStrategy):
    def resolve(
        self, *, tree: FactTree, fw: EffectiveFramework, repo_id: str
    ) -> list[ResolvedEndpoint]:
        routes = fw.routes
        if not isinstance(routes, RoutePatterns):
            return []
        base_path, base_derivation = _base_path(tree, routes)
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
            prefix, mount_derivation = _pick_mount(mount_prefixes, receiver, dec.file)
            full = _join_paths(base_path, prefix, path)
            derivation = (*base_derivation, *mount_derivation, dec.id)

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


# Register for every Python decorator-driven framework we know.
register("fastapi", PythonDecoratorEndpointStrategy)
register("flask", PythonDecoratorEndpointStrategy)
