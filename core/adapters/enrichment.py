"""Post-merge enrichment passes.

Adapters run in isolation — Datadog doesn't see testparser's Endpoint nodes
while it's parsing spans, and vice versa. After merge, we can join across
adapter outputs to materialize cross-adapter edges that no single adapter
could produce alone.

Right now we run one pass:
  * `link_connections_to_endpoints`: join ExternalConnection.endpoint
    (e.g. "GET /charges") against Endpoint(method, path) within the
    target service, populating `target_endpoint_id` so the graph can
    answer "which endpoint is this traced call hitting?"
"""

from __future__ import annotations

import logging

from core.adapters.merger import MergedResult
from core.types import Endpoint

logger = logging.getLogger(__name__)


def link_connections_to_endpoints(merged: MergedResult) -> int:
    """Join traced ExternalConnections to static-analysis Endpoints.

    Returns the number of connections that gained a `target_endpoint_id`.
    Mutates `merged.connections` in place by swapping in `model_copy`-ed
    objects (the Pydantic models are frozen).
    """
    index = _build_endpoint_index(merged.endpoints.values())
    if not index:
        return 0

    linked = 0
    for conn_id, conn in list(merged.connections.items()):
        if conn.target_endpoint_id or not conn.target_service_id:
            continue
        parsed = _parse_method_path(conn.endpoint)
        if parsed is None:
            continue
        method, path = parsed
        endpoint_id = (
            index.get((conn.target_service_id, method, path))
            or _suffix_match(index, conn.target_service_id, method, path)
        )
        if endpoint_id is None:
            continue
        merged.connections[conn_id] = conn.model_copy(
            update={"target_endpoint_id": endpoint_id}
        )
        linked += 1
    if linked:
        logger.info("enrichment: linked %d connections to endpoints", linked)
    return linked


def _build_endpoint_index(
    endpoints,  # type: ignore[no-untyped-def]
) -> dict[tuple[str, str, str], str]:
    """`(repo_id, method, path) -> endpoint_id`. Method is uppercased."""
    out: dict[tuple[str, str, str], str] = {}
    for ep in endpoints:
        out[(ep.repo_id, ep.method.upper(), ep.path)] = ep.id
    return out


def _parse_method_path(endpoint_text: str) -> tuple[str, str] | None:
    """Split Datadog's `resource_name` into (METHOD, path).

    Datadog HTTP resources look like 'GET /users/{id}'. Anything that
    doesn't fit that shape (DB queries, RPC verbs, free text) returns
    None — those connections won't link to an Endpoint, which is the
    correct outcome since Endpoint nodes are HTTP/RPC routes only.
    """
    text = (endpoint_text or "").strip()
    if not text or " " not in text:
        return None
    method, _, path = text.partition(" ")
    method = method.upper()
    if not method.isalpha() or not path.startswith("/"):
        return None
    return method, path


def _suffix_match(
    index: dict[tuple[str, str, str], str],
    service_id: str,
    method: str,
    traced_path: str,
) -> str | None:
    """Fallback: handle indexed paths that carry an unresolved framework
    prefix (e.g. `<attr:settings.FASTAPI_API_V1_PATH>/sys/menus/{pk}`)
    by matching the indexed path's suffix against the traced path.

    Only considers indexed paths whose first segment is a placeholder
    (`<...>` or `${...}`) — won't blindly suffix-match arbitrary routes.
    """
    for (svc, m, indexed_path), ep_id in index.items():
        if svc != service_id or m != method:
            continue
        if not _has_unresolved_prefix(indexed_path):
            continue
        if indexed_path.endswith(traced_path):
            return ep_id
    return None


def _has_unresolved_prefix(path: str) -> bool:
    if not path.startswith("/"):
        return False
    second_slash = path.find("/", 1)
    head = path[1:second_slash] if second_slash > 0 else path[1:]
    return head.startswith("<") or head.startswith("${")
