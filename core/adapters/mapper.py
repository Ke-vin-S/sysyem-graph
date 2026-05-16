"""Link source-code artifacts to runtime ExternalConnections."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from core.types import CodeArtifact, ExternalConnection


@dataclass
class _Endpoint:
    method: str
    path: str

    @classmethod
    def parse(cls, raw: str) -> "_Endpoint | None":
        if not raw:
            return None
        parts = raw.strip().split(maxsplit=1)
        if len(parts) == 2:
            return cls(method=parts[0].upper(), path=parts[1])
        return cls(method="*", path=parts[0])


class ArtifactConnectionMapper:
    """Match CodeArtifacts (typically HTTP endpoint handlers) to ExternalConnections.

    Datadog gives us 'service X called POST /api/charges on service Y'. GitHub
    static analysis gives us 'service Y has a handler `create_charge` at line 42'.
    This mapper joins them: every endpoint artifact gets the IDs of the
    connections that target it.

    Path-template matching is intentionally simple: convert `/users/{id}` to a
    regex once, test against the observed `/users/42`. Anything beyond that
    (RFC 6570 templates, gRPC streaming, etc.) is out of scope here.
    """

    def map(
        self,
        artifacts: Iterable[CodeArtifact],
        connections: Iterable[ExternalConnection],
    ) -> list[CodeArtifact]:
        connections = list(connections)
        out: list[CodeArtifact] = []
        for artifact in artifacts:
            if artifact.type != "endpoint":
                out.append(artifact)
                continue
            matched = self._match_endpoint(artifact, connections)
            if not matched:
                out.append(artifact)
                continue
            merged_ids = tuple(sorted(set(artifact.external_connections) | matched))
            out.append(artifact.model_copy(update={"external_connections": merged_ids}))
        return out

    def _match_endpoint(
        self, artifact: CodeArtifact, connections: list[ExternalConnection]
    ) -> set[str]:
        endpoint = _Endpoint.parse(artifact.name)
        if endpoint is None:
            return set()
        artifact_re = _path_to_regex(endpoint.path)
        matched: set[str] = set()
        for conn in connections:
            if conn.target_service_id != artifact.repo_id:
                continue
            conn_ep = _Endpoint.parse(conn.endpoint)
            if conn_ep is None:
                continue
            if endpoint.method not in ("*", conn_ep.method):
                continue
            if artifact_re.fullmatch(conn_ep.path):
                matched.add(conn.id)
        return matched


_PARAM = re.compile(r"\{[^/}]+\}")


def _path_to_regex(template: str) -> re.Pattern[str]:
    escaped = re.escape(template)
    # Undo the escaping of `{name}` segments and replace with a wildcard.
    pattern = _PARAM.sub(r"[^/]+", escaped.replace(r"\{", "{").replace(r"\}", "}"))
    return re.compile(pattern)
