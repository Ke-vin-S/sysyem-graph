"""Tests for /api/pipelines/* — must work without Neo4j or external services.

The pipeline service reads SQLite stores; it has to handle the
'no store yet' case gracefully because that's the state right after a
fresh checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Point all adapter stores at the temp dir so we don't read real
    # local state. None of these files will exist — that's the test.
    monkeypatch.setenv("DD_STORE_PATH", str(tmp_path / "datadog.db"))
    monkeypatch.setenv("GITHUB_STORE_PATH", str(tmp_path / "github.db"))
    monkeypatch.setenv("TESTPARSER_ROOT", str(tmp_path / "nonexistent"))
    # Clear the lru_cache so the new env vars take effect for this test.
    from core.config import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    return TestClient(create_app())


def test_list_pipelines_returns_three_adapters(client: TestClient) -> None:
    response = client.get("/api/pipelines")
    assert response.status_code == 200
    body = response.json()
    ids = {p["id"] for p in body["pipelines"]}
    assert ids == {"github", "datadog", "testparser"}


def test_pipeline_summaries_handle_missing_stores(client: TestClient) -> None:
    """No stores exist on disk yet → endpoints must still respond 200."""
    response = client.get("/api/pipelines/github")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "github"
    assert body["repos"] == []


def test_pipeline_datadog_disabled_without_keys(client: TestClient) -> None:
    response = client.get("/api/pipelines/datadog")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["status"] == "disabled"


def test_pipeline_testparser_reports_missing_root(client: TestClient) -> None:
    response = client.get("/api/pipelines/testparser")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "testparser"
    assert body["exists"] is False


def test_unknown_pipeline_404s(client: TestClient) -> None:
    response = client.get("/api/pipelines/nope")
    assert response.status_code == 404
