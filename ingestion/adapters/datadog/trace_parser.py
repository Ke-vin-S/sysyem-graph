"""Aggregate raw Datadog spans into Service/ExternalConnection records.

Spans arrive one-by-one; we bucket them by (source, target, endpoint) and emit
one ExternalConnection per bucket with the aggregate frequency and timing.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from core.types import (
    ContractStatus,
    Criticality,
    Direction,
    ExternalConnection,
    Service,
)
from ingestion.adapters.datadog.client import RawSpan


@dataclass
class _Bucket:
    source: str
    target: str
    endpoint: str
    protocol: str
    span_type: str
    target_is_service: bool
    """True if the target is a tracked service; False for infra (DB, queue, cache)."""
    count: int = 0
    errors: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None


@dataclass
class ParseResult:
    services: list[Service] = field(default_factory=list)
    connections: list[ExternalConnection] = field(default_factory=list)
    spans_seen: int = 0
    spans_skipped: int = 0


class TraceParser:
    """Roll up raw spans into the impact-graph node model."""

    def __init__(self, *, lookback_hours: int, min_span_count: int = 1) -> None:
        self._lookback_hours = lookback_hours
        self._min_span_count = min_span_count

    def parse(self, spans: Iterable[RawSpan], *, now: datetime | None = None) -> ParseResult:
        now = now or datetime.now(timezone.utc)
        result = ParseResult()
        services_seen: dict[str, datetime] = {}
        buckets: dict[tuple[str, str, str], _Bucket] = {}

        for span in spans:
            result.spans_seen += 1
            target, target_is_service = span.resolve_target()
            # Always remember the source service as observed.
            services_seen.setdefault(span.service, span.start)
            services_seen[span.service] = max(services_seen[span.service], span.start)
            if not target or target == span.service:
                # Internal span (no cross-service edge); source already recorded.
                result.spans_skipped += 1
                continue

            # Only register the target as a Service node when it actually is one.
            # Infrastructure targets (DB hosts, kafka topics, redis clusters) are
            # recorded as ExternalConnection.target_name only — adding them as
            # Service nodes would pollute the service inventory.
            if target_is_service:
                services_seen.setdefault(target, span.start)
                services_seen[target] = max(services_seen[target], span.start)

            endpoint = _normalize_endpoint(span)
            protocol = _classify_protocol(span)
            key = (span.service, target, endpoint)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = _Bucket(
                    source=span.service,
                    target=target,
                    endpoint=endpoint,
                    protocol=protocol,
                    span_type=span.type or "unknown",
                    target_is_service=target_is_service,
                    first_seen=span.start,
                    last_seen=span.start,
                )
                buckets[key] = bucket
            bucket.count += 1
            if span.error:
                bucket.errors += 1
            if bucket.first_seen is None or span.start < bucket.first_seen:
                bucket.first_seen = span.start
            if bucket.last_seen is None or span.start > bucket.last_seen:
                bucket.last_seen = span.start

        result.services = [
            _service_from_id(svc, last_seen=last) for svc, last in sorted(services_seen.items())
        ]
        window = timedelta(hours=self._lookback_hours)
        for bucket in buckets.values():
            if bucket.count < self._min_span_count:
                continue
            result.connections.append(_connection_from_bucket(bucket, window=window, now=now))
        result.connections.sort(key=lambda c: c.id)
        return result


def _normalize_endpoint(span: RawSpan) -> str:
    resource = (span.resource or "").strip()
    if not resource:
        return span.operation or "unknown"
    # 'POST /charges/123' -> 'POST /charges/{id}' isn't possible without route
    # tables; rely on Datadog's resource_name which is usually already templated.
    return resource


def _classify_protocol(span: RawSpan) -> str:
    if span.type:
        return span.type
    op = (span.operation or "").lower()
    if "http" in op:
        return "http"
    if "grpc" in op:
        return "grpc"
    if "kafka" in op:
        return "kafka"
    if "sql" in op or "postgres" in op or "mysql" in op:
        return "sql"
    return "unknown"


def _service_from_id(service_id: str, *, last_seen: datetime) -> Service:
    return Service(
        id=service_id,
        name=service_id,
        repoUrl=f"unknown://{service_id}",
        language="unknown",
        framework="unknown",
        owner="unknown",
        createdAt=last_seen,
        lastUpdatedAt=last_seen,
        isActive=True,
    )


def _connection_from_bucket(
    bucket: _Bucket, *, window: timedelta, now: datetime
) -> ExternalConnection:
    minutes = max(1.0, window.total_seconds() / 60.0)
    frequency = bucket.count / minutes
    error_rate = bucket.errors / bucket.count if bucket.count else 0.0
    return ExternalConnection(
        id=_connection_id(bucket),
        type=_edge_type(bucket.protocol),
        sourceServiceId=bucket.source,
        targetServiceId=bucket.target if bucket.target_is_service else None,
        targetName=bucket.target,
        protocol=bucket.protocol,
        endpoint=bucket.endpoint,
        direction=Direction.OUTBOUND,
        frequency=round(frequency, 4),
        criticality=_classify_criticality(frequency, error_rate),
        contractStatus=ContractStatus.UNKNOWN,
        dataFlow={"error_rate": f"{error_rate:.4f}", "spans_observed": str(bucket.count)},
        discoveredAt=bucket.first_seen or now,
        lastObservedAt=bucket.last_seen or now,
    )


def _connection_id(bucket: _Bucket) -> str:
    payload = f"{bucket.source}|{bucket.target}|{bucket.endpoint}".encode()
    return "conn:" + hashlib.sha1(payload).hexdigest()[:16]


def _edge_type(protocol: str) -> str:
    if protocol in {"http", "https"}:
        return "http"
    if protocol == "grpc":
        return "grpc"
    if protocol == "kafka":
        return "kafka"
    if protocol == "sql":
        return "sql"
    return protocol or "unknown"


def _classify_criticality(frequency_per_min: float, error_rate: float) -> Criticality:
    if frequency_per_min >= 100 or error_rate >= 0.05:
        return Criticality.CRITICAL
    if frequency_per_min >= 10:
        return Criticality.HIGH
    if frequency_per_min >= 1:
        return Criticality.MEDIUM
    return Criticality.LOW
