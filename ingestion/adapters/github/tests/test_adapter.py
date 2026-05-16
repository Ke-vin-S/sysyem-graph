"""Unit tests for the GitHub adapter (uses an in-memory stub client)."""

from __future__ import annotations

from datetime import datetime, timezone

from core.adapters import IngestionContext
from ingestion.adapters.github import (
    GitHubAdapter,
    GitHubAdapterConfig,
    GitHubClient,
    RepoFile,
    RepoSnapshot,
)

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)

SAMPLE_HANDLER = """\
from fastapi import APIRouter

router = APIRouter()


@router.post("/charges")
def create_charge(payload):
    return {"ok": True}


def _internal_helper():
    pass


def compute_fee(amount):
    return amount * 0.03
"""


class _StubClient(GitHubClient):
    def __init__(self, snapshots: dict[str, RepoSnapshot]) -> None:
        super().__init__(token="x")
        self._snapshots = snapshots

    def fetch_repo(self, full_name, *, file_extensions, max_file_bytes):  # type: ignore[override]
        return self._snapshots[full_name]


def test_github_adapter_emits_service_and_endpoint_artifact() -> None:
    snapshot = RepoSnapshot(
        full_name="acme/payment-service",
        default_branch="main",
        clone_url="https://github.com/acme/payment-service.git",
        language="Python",
        owner="acme",
        created_at=NOW,
        pushed_at=NOW,
        files=[
            RepoFile(path="src/handlers.py", size=len(SAMPLE_HANDLER), sha="abc", content=SAMPLE_HANDLER)
        ],
    )
    client = _StubClient({"acme/payment-service": snapshot})
    config = GitHubAdapterConfig(token="x", repos=("acme/payment-service",))
    adapter = GitHubAdapter(config, client=client)

    result = adapter.extract(IngestionContext(now=NOW))
    assert len(result.services) == 1
    svc = result.services[0]
    assert svc.id == "acme/payment-service"
    assert svc.language == "Python"

    endpoint_artifacts = [a for a in result.artifacts if a.type == "endpoint"]
    function_artifacts = [a for a in result.artifacts if a.type == "function"]
    assert any(a.name == "POST /charges" for a in endpoint_artifacts)
    assert any(a.name == "compute_fee" for a in function_artifacts)
    assert all(not a.name.startswith("_") for a in function_artifacts)


def test_github_adapter_requires_repos() -> None:
    import pytest

    with pytest.raises(ValueError):
        GitHubAdapter(GitHubAdapterConfig(token="x", repos=()))
