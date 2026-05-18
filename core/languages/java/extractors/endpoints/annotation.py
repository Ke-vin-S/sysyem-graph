"""JavaAnnotationEndpointStrategy — Spring Web MVC and friends.

Annotation-on-method routes (`@GetMapping("/x")`) composed with a
class-level prefix annotation (`@RequestMapping("/users")` on the
controller class) and a base path from `application.yml`. The mapping
from annotation name (`GetMapping`) to HTTP method (`get`) is
*declared in the framework YAML*, not hardcoded here — see
`RoutePatterns.annotation_method_map` and `frameworks/java/spring.yaml`.

Future annotation-driven frameworks (Spring Boot WebFlux, Quarkus,
Micronaut, JAX-RS) plug in by registering this same class against their
framework name once the YAML covers their patterns.
"""

from __future__ import annotations

from typing import Any

from core.facts import FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import RoutePatterns
from core.resolvers.endpoints.strategy import EndpointStrategy, register
from core.resolvers.endpoints.types import ResolvedEndpoint


class JavaAnnotationEndpointStrategy(EndpointStrategy):
    def resolve(
        self, *, tree: FactTree, fw: EffectiveFramework, repo_id: str
    ) -> list[ResolvedEndpoint]:
        routes = fw.routes
        if not isinstance(routes, RoutePatterns):
            return []
        base_path, base_derivation = _base_path(tree, routes)

        # Index class annotations to find class-level prefixes.
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
            method = _annotation_method(callee, ann.data, routes)
            if not method:
                continue
            path = _first_string_arg(ann.data) or "/"

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


# ---- helpers --------------------------------------------------------------


def _base_path(tree: FactTree, routes: RoutePatterns) -> tuple[str, tuple[str, ...]]:
    keys = [src.config_key for src in routes.base_path_sources if src.config_key]
    for fact in tree.where(kind=FactKind.CONFIG_VALUE):
        if fact.data.get("key") in keys:
            value = str(fact.data.get("value", ""))
            if value.startswith("/"):
                return value, (fact.id,)
    return "", ()


def _annotation_method(
    callee: str, data: dict[str, Any], routes: RoutePatterns
) -> str:
    """Resolve the HTTP method for an annotation.

    Priority:
      1. YAML-declared `annotation_method_map` (Spring: GetMapping → get).
      2. `method=` kwarg on the annotation (e.g. `@RequestMapping(method=GET)`),
         normalized to lowercase.
      3. `"any"` for `RequestMapping` with no method — matches Spring's
         "any method" semantic.
    """
    mapped = routes.annotation_method_map.get(callee)
    if mapped:
        return mapped
    kwargs = data.get("kwargs") or {}
    method_kwarg = kwargs.get("method")
    if isinstance(method_kwarg, str) and method_kwarg:
        return method_kwarg.split(".")[-1].lower()
    if callee == "RequestMapping":
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


def _join_paths(*parts: str) -> str:
    joined: list[str] = []
    for raw in parts:
        if not raw:
            continue
        joined.append(raw.strip("/"))
    return "/" + "/".join(p for p in joined if p)


# Register for known annotation-driven frameworks. Extend as more land.
register("spring", JavaAnnotationEndpointStrategy)
