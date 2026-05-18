"""ConfigBindingResolver: discover service dependencies declared in config.

Many cross-service connections aren't visible in code at all — they live
as a URL/host/topic string in `application.yml`, `.env`, or
`config.toml`, and the code just references the config key
(`@Value("${billing.url}")`, `os.environ["BILLING_URL"]`,
`settings.billing_url`). Datadog catches these at runtime but they're
also recoverable statically.

This resolver walks CONFIG_VALUE facts and emits ExternalConnection
records when a value looks like a URL or `host:port` reference. The
extracted `target_name` is the URL's hostname (or the bare host string)
— it's the cross-repo join key that lets us later stitch
(source_service)-[:INITIATES]->(connection)-[:TARGETS]->(target_service)
once another service registers as `target_name`.

Out of scope for v1:
  * Verifying the key is *referenced* by code (`@Value("${key}")`,
    `os.getenv("KEY")`). Without that filter we may emit connections
    for unused config — better to be inclusive now; tightening is
    additive later.
  * Resolving env-var placeholders (`${DB_URL}` in YAML pointing to an
    actual URL in `.env`). Most repos either inline the URL or use
    env-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from core.facts import FactKind, FactTree
from core.types import (
    ContractStatus,
    Criticality,
    Direction,
    ExternalConnection,
)


@dataclass
class ConfigBindingResolution:
    connections: list[ExternalConnection]


# A value is treated as a URL when urlparse extracts a hostname for it.
# We also accept bare `host:port` strings for plain-tcp services
# (kafka.broker.local:9092, redis-prod:6379).
_HOST_PORT_RE = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9._-]*):(\d{2,5})$")

_INTERESTING_KEY_HINTS = (
    "url",
    "uri",
    "host",
    "endpoint",
    "bootstrap.servers",
    "bootstrap-servers",
    "datasource.url",
    "broker",
)

# We deliberately don't try to classify the protocol here — let the
# loader/queries decide. Defaults below are conservative.


class ConfigBindingResolver:
    def resolve(
        self,
        *,
        tree: FactTree,
        repo_id: str,
        source_service_id: str,
        now: datetime | None = None,
    ) -> ConfigBindingResolution:
        when = now or datetime.now(timezone.utc)
        seen: set[str] = set()
        connections: list[ExternalConnection] = []
        for fact in tree.where(kind=FactKind.CONFIG_VALUE):
            key = str(fact.data.get("key", ""))
            value = str(fact.data.get("value", ""))
            host, port, proto = _parse_target(key, value)
            if not host:
                continue
            conn_id = f"conn:cfg:{repo_id}:{key}:{host}"
            if conn_id in seen:
                continue
            seen.add(conn_id)
            connections.append(
                ExternalConnection(
                    id=conn_id,
                    type=proto or "http",
                    sourceServiceId=source_service_id,
                    targetServiceId=None,
                    targetName=host,
                    protocol=proto or "http",
                    endpoint=f"{host}:{port}" if port else host,
                    direction=Direction.OUTBOUND,
                    frequency=0.0,
                    criticality=Criticality.MEDIUM,
                    contractStatus=ContractStatus.UNKNOWN,
                    dataFlow={"source": "config", "config_key": key, "config_file": fact.file},
                    discoveredAt=when,
                    lastObservedAt=when,
                )
            )
        return ConfigBindingResolution(connections=connections)


def _parse_target(key: str, value: str) -> tuple[str, str, str]:
    """Return (host, port, protocol). Empty host means 'not a target'.

    The key is consulted as a hint: even if the value parses as a URL,
    we only treat it as a service target when the key suggests it
    (avoids picking up file paths that happen to look URL-shaped).
    """
    if not value:
        return "", "", ""
    key_lower = key.lower().replace("_", ".")
    if not any(hint in key_lower for hint in _INTERESTING_KEY_HINTS):
        return "", "", ""
    # JDBC URLs (`jdbc:postgresql://host:port/db`) confuse urlparse because
    # the first colon makes the scheme "jdbc". Strip that prefix so the
    # inner scheme parses cleanly.
    normalized = value[5:] if value.lower().startswith("jdbc:") else value
    parsed = urlparse(normalized)
    if parsed.scheme and parsed.hostname:
        return parsed.hostname, str(parsed.port or ""), parsed.scheme
    # Fall back to `host:port` bare form.
    match = _HOST_PORT_RE.match(value)
    if match:
        return match.group(1), match.group(2), ""
    return "", "", ""
