"""Shared pytest fixtures for core tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.types import (
    CodeArtifact,
    Direction,
    ExternalConnection,
    LineRange,
    Service,
    TestCase,
    TestType,
)

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def service_auth() -> Service:
    return Service(
        id="auth-service",
        name="auth-service",
        repoUrl="https://example.com/auth-service",
        language="python",
        framework="fastapi",
        owner="team-platform",
        createdAt=NOW,
        lastUpdatedAt=NOW,
        isActive=True,
    )


@pytest.fixture
def service_payment() -> Service:
    return Service(
        id="payment-service",
        name="payment-service",
        repoUrl="https://example.com/payment-service",
        language="go",
        framework="gin",
        owner="team-payments",
        createdAt=NOW,
        lastUpdatedAt=NOW,
        isActive=True,
    )


@pytest.fixture
def connection_auth_to_payment() -> ExternalConnection:
    return ExternalConnection(
        id="conn:auth->payment",
        type="http",
        sourceServiceId="auth-service",
        targetServiceId="payment-service",
        targetName="payment-service",
        protocol="http",
        endpoint="POST /charges",
        direction=Direction.OUTBOUND,
        frequency=120.0,
        discoveredAt=NOW,
        lastObservedAt=NOW,
    )


@pytest.fixture
def artifact_payment_endpoint() -> CodeArtifact:
    return CodeArtifact(
        id="endpoint:payment-service:POST:/charges",
        repoId="payment-service",
        type="endpoint",
        name="POST /charges",
        file="src/handlers.go",
        lineRange=LineRange(start=42, end=42),
        isPublic=True,
    )


@pytest.fixture
def test_case_auth_unit() -> TestCase:
    return TestCase(
        id="test:auth-service:tests/test_jwt.py:test_verify",
        repoId="auth-service",
        type=TestType.UNIT,
        name="test_verify",
        file="tests/test_jwt.py",
        lineRange=LineRange(start=10, end=20),
        duration_ms=150,
        flakiness_score=0.0,
        affectedRepos=("auth-service",),
    )
