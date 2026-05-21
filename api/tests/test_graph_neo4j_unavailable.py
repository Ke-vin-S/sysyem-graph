"""Graph endpoints when Neo4j is unreachable — must respond cleanly.

Both tests force `NEO4J_URI` to a port nothing's listening on, so they
behave identically whether or not the developer has a local Neo4j
running on the default 7687.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


@pytest.fixture()
def dead_neo4j(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Reserved-for-blackhole port: nothing should ever listen here.
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    from core.config import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    return TestClient(create_app())


def test_counts_returns_zeros_when_neo4j_down(dead_neo4j: TestClient) -> None:
    """/counts is special — it's called on every page load to render a
    badge, so it gracefully returns zeros when the DB is unreachable
    instead of 503-ing the UI."""
    response = dead_neo4j.get("/api/graph/counts")
    assert response.status_code == 200
    body = response.json()
    assert body["services"] == 0
    assert body["artifacts"] == 0


def test_protected_graph_endpoints_503_when_neo4j_down(dead_neo4j: TestClient) -> None:
    """The data endpoints DO 503 — there's nothing they can return when
    the graph is gone, and the UI handles 503 by showing a banner."""
    response = dead_neo4j.get("/api/graph/services")
    assert response.status_code == 503
