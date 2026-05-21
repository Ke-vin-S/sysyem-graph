"""Tests for /health endpoints — must work without Neo4j running."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import create_app


def test_healthz_is_always_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_neo4j_health_is_a_probe_not_a_gate() -> None:
    """The /health/neo4j endpoint MUST NOT 5xx when Neo4j is unreachable —
    the UI uses it to render a connection banner, so we have to surface
    `reachable: false` as a 200."""
    client = TestClient(create_app())
    response = client.get("/health/neo4j")
    assert response.status_code == 200
    body = response.json()
    assert "reachable" in body
    assert "uri" in body
    assert "database" in body
