"""EndpointResolver: reconstruct full HTTP paths across files.

The hard part isn't finding `@router.get("/users")` — that's one fact. The
hard part is composing it with `FastAPI(root_path="/v1")` from `main.py`
and `include_router(router, prefix="/payments")` from somewhere else into
`GET /v1/payments/users`.

This resolver walks DECORATOR / ANNOTATION / CALL / CONFIG_VALUE / SYMBOL
facts and joins them. It targets three frameworks in this first cut:
FastAPI, Flask, and Spring. Every output carries a `derivation` tuple of
fact IDs so a reviewer can replay exactly which facts contributed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.facts import Fact, FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import RoutePatterns
from core.resolvers.resolver import ResolverContext

logger = logging.getLogger(__name__)


@dataclass
class ResolvedEndpoint:
    """One reconstructed HTTP endpoint.

    `derivation` is the audit trail — every fact ID that contributed. This
    is the receipt the user asked for: "why does this endpoint look like
    `GET /v1/payments/users/{id}`?" -> `tree.get(fact_id)` per entry.
    """

    method: str
    full_path: str
    handler_file: str
    handler_symbol: str
    framework: str
    confidence: float = 1.0
    derivation: tuple[str, ...] = field(default_factory=tuple)


class EndpointResolver:
    def resolve(self, context: ResolverContext) -> list[ResolvedEndpoint]:
        results: list[ResolvedEndpoint] = []
        for fw in context.frameworks:
            if fw.routes is None:
                continue
            if fw.language == "python":
                results.extend(self._resolve_python(context.tree, fw, context.repo_id))
            elif fw.language == "java":
                results.extend(self._resolve_java(context.tree, fw, context.repo_id))
        return results

    # ----------------------------------------------------------------- Python

    def _resolve_python(
        self, tree: FactTree, fw: EffectiveFramework, repo_id: str
    ) -> list[ResolvedEndpoint]:
        routes = fw.routes
        assert isinstance(routes, RoutePatterns)
        base_path, base_derivation = _python_base_path(tree, routes)
        mount_prefixes = _python_mount_prefixes(tree, routes)

        results: list[ResolvedEndpoint] = []
        for dec in tree.where(kind=FactKind.DECORATOR):
            callee = str(dec.data.get("callee", ""))
            if not _matches_decorator_pattern(callee, routes.decorator_callee_patterns):
                continue
            method = _python_decorator_method(callee, routes)
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

    # ------------------------------------------------------------------- Java

    def _resolve_java(
        self, tree: FactTree, fw: EffectiveFramework, repo_id: str
    ) -> list[ResolvedEndpoint]:
        routes = fw.routes
        assert isinstance(routes, RoutePatterns)
        base_path, base_derivation = _java_base_path(tree, routes)

        # Index class annotations to find class-level @RequestMapping prefixes.
        class_prefixes: dict[tuple[str, str], tuple[str, str]] = {}
        for ann in tree.where(kind=FactKind.ANNOTATION):
            callee = ann.data.get("callee", "")
            if callee not in routes.annotation_class_prefix:
                continue
            if ann.data.get("target_kind") != "class":
                continue
            class_name = ann.data.get("target_symbol", "")
            path = _first_string_arg(ann.data)
            if path is None:
                continue
            class_prefixes[(ann.file, class_name)] = (path, ann.id)

        results: list[ResolvedEndpoint] = []
        for ann in tree.where(kind=FactKind.ANNOTATION):
            callee = ann.data.get("callee", "")
            if callee not in routes.annotation_method_names:
                continue
            if ann.data.get("target_kind") != "method":
                continue
            method = _spring_method_from_annotation(callee, ann.data)
            if not method:
                continue
            path = _first_string_arg(ann.data) or "/"

            # Find the symbol this annotation targets, and its enclosing class.
            handler_name = ann.data.get("target_symbol", "")
            handler_symbol = next(
                (
                    s
                    for s in tree.where(kind=FactKind.SYMBOL, file=ann.file)
                    if s.data.get("name") == handler_name
                    and s.data.get("sym_kind") == "method"
                ),
                None,
            )
            class_prefix = ""
            class_derivation: tuple[str, ...] = ()
            if handler_symbol is not None:
                enclosing = tree.enclosing_class(handler_symbol)
                if enclosing is not None:
                    class_name = enclosing.data.get("name", "")
                    found = class_prefixes.get((ann.file, class_name))
                    if found is not None:
                        class_prefix, class_ann_id = found
                        class_derivation = (class_ann_id, enclosing.id)
            full = _join_paths(base_path, class_prefix, path)
            derivation = (*base_derivation, *class_derivation, ann.id)
            if handler_symbol is not None:
                derivation = (*derivation, handler_symbol.id)
            results.append(
                ResolvedEndpoint(
                    method=method.upper(),
                    full_path=full,
                    handler_file=ann.file,
                    handler_symbol=handler_name,
                    framework=fw.name,
                    derivation=derivation,
                )
            )
        return results


# -------------------------------------------------------- helpers (Python)


def _python_base_path(tree: FactTree, routes: RoutePatterns) -> tuple[str, tuple[str, ...]]:
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
    """The variable name that the decorator's receiver should match
    (e.g. `router` for `@router.get(...)`)."""

    module_hint: str
    """When the include call wrote `charges.router`, this is `charges` —
    the source module the router lives in. Empty when the include used a
    bare name (single-file pattern)."""

    prefix: str
    call_id: str


def _python_mount_prefixes(
    tree: FactTree, routes: RoutePatterns
) -> list[_MountInfo]:
    """Build a list of mounts for later (receiver, file-stem) matching.

    Two real-world patterns we need to support:

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
    """Choose the right mount for a decorator's receiver.

    Preference order: (1) mount with `module_hint == file stem` and matching
    receiver; (2) mount with empty `module_hint` and matching receiver
    (single-file pattern); (3) no match → empty prefix.
    """
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


def _python_decorator_method(callee: str, routes: RoutePatterns) -> str:
    """`router.get` -> `get`; respects the framework's declared methods."""
    method = callee.rsplit(".", 1)[-1]
    if routes.decorator_methods and method not in routes.decorator_methods:
        return ""
    return method


def _matches_decorator_pattern(callee: str, patterns: tuple[str, ...]) -> bool:
    """`router.get` matches `{any}.get`. `{any}` means "any segment up to the dot"."""
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


# ---------------------------------------------------------- helpers (Java)


def _java_base_path(tree: FactTree, routes: RoutePatterns) -> tuple[str, tuple[str, ...]]:
    keys = [src.config_key for src in routes.base_path_sources if src.config_key]
    for fact in tree.where(kind=FactKind.CONFIG_VALUE):
        if fact.data.get("key") in keys:
            value = str(fact.data.get("value", ""))
            if value.startswith("/"):
                return value, (fact.id,)
    return "", ()


def _spring_method_from_annotation(callee: str, data: dict[str, Any]) -> str:
    mapping = {
        "GetMapping": "get",
        "PostMapping": "post",
        "PutMapping": "put",
        "DeleteMapping": "delete",
        "PatchMapping": "patch",
    }
    if callee in mapping:
        return mapping[callee]
    if callee == "RequestMapping":
        kwargs = data.get("kwargs") or {}
        method_kwarg = kwargs.get("method")
        if isinstance(method_kwarg, str) and method_kwarg:
            return method_kwarg.split(".")[-1].lower()
        # Default for RequestMapping with no method= is "*"
        return "any"
    return ""


def _first_string_arg(data: dict[str, Any]) -> str | None:
    for value in data.get("args") or []:
        if isinstance(value, str) and value.startswith("/"):
            return value
    kwargs = data.get("kwargs") or {}
    for key in ("value", "path"):
        candidate = kwargs.get(key)
        if isinstance(candidate, str) and candidate.startswith("/"):
            return candidate
    return None


# ---------------------------------------------------------- path joiner


def _join_paths(*parts: str) -> str:
    joined: list[str] = []
    for raw in parts:
        if not raw:
            continue
        joined.append(raw.strip("/"))
    out = "/" + "/".join(p for p in joined if p)
    return out
