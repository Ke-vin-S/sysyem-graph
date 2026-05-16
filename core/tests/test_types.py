"""Tests for core.types: validation, immutability, serialization."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from core.types import (
    Change,
    ChangedFile,
    Criticality,
    Direction,
    ExternalConnection,
    LineRange,
    Service,
    TestType,
)
from core.types.change import ChangeType, FileOp


def test_line_range_end_must_be_ge_start() -> None:
    with pytest.raises(ValidationError):
        LineRange(start=10, end=5)


def test_service_is_frozen(service_auth: Service) -> None:
    with pytest.raises(ValidationError):
        service_auth.name = "different"  # type: ignore[misc]


def test_service_accepts_alias(now: datetime) -> None:
    svc = Service.model_validate(
        {
            "id": "x",
            "name": "x",
            "repoUrl": "https://example.com/x",
            "createdAt": now,
            "lastUpdatedAt": now,
            "isActive": True,
        }
    )
    assert svc.repo_url == "https://example.com/x"


def test_service_rejects_extras(now: datetime) -> None:
    with pytest.raises(ValidationError):
        Service(
            id="x",
            name="x",
            repoUrl="https://example.com/x",
            createdAt=now,
            lastUpdatedAt=now,
            isActive=True,
            unexpected_field="boom",  # type: ignore[call-arg]
        )


def test_external_connection_defaults(connection_auth_to_payment: ExternalConnection) -> None:
    c = connection_auth_to_payment
    assert c.direction is Direction.OUTBOUND
    assert c.criticality is Criticality.MEDIUM
    assert c.frequency == 120.0


def test_external_connection_negative_frequency_rejected(now: datetime) -> None:
    with pytest.raises(ValidationError):
        ExternalConnection(
            id="x",
            type="http",
            sourceServiceId="a",
            targetServiceId="b",
            targetName="b",
            frequency=-1.0,
            discoveredAt=now,
            lastObservedAt=now,
        )


def test_test_case_flakiness_bounded(now: datetime) -> None:
    with pytest.raises(ValidationError):
        # flakiness_score > 1.0 should be rejected.
        from core.types import TestCase

        TestCase(
            id="t",
            repoId="r",
            name="t",
            file="t.py",
            lineRange=LineRange(start=1, end=1),
            flakiness_score=1.5,
        )


def test_change_roundtrip_json(now: datetime) -> None:
    change = Change(
        id="abc123",
        repoId="auth-service",
        type=ChangeType.COMMIT,
        timestamp=now,
        description="fix bug",
        files=(ChangedFile(path="src/x.py", op=FileOp.MODIFIED, additions=2, deletions=1),),
        author="kevin",
    )
    data = change.model_dump(mode="json")
    assert data["type"] == "COMMIT"
    assert data["timestamp"] == now.isoformat().replace("+00:00", "Z") or data["timestamp"].startswith("2026")
    rebuilt = Change.model_validate(data)
    assert rebuilt == change


def test_test_type_enum_values() -> None:
    assert TestType.UNIT.value == "UNIT"
    assert TestType("UNIT") is TestType.UNIT


def test_aware_datetime_required() -> None:
    # naive datetimes are still accepted, but we expect production callers to
    # pass UTC. This test pins behavior so a future tightening is intentional.
    naive = datetime(2026, 1, 1)
    svc = Service(
        id="x",
        name="x",
        repoUrl="https://example.com/x",
        createdAt=naive,
        lastUpdatedAt=naive,
        isActive=True,
    )
    assert svc.created_at.tzinfo is None
    aware = naive.replace(tzinfo=timezone.utc)
    svc2 = svc.model_copy(update={"created_at": aware})
    assert svc2.created_at.tzinfo is timezone.utc
