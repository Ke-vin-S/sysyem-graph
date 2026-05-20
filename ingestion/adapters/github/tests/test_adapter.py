"""Unit tests for the clone-based GitHubAdapter.

We don't shell out to `git` here — those flows live in test_cloner.py /
test_service.py. Instead we stub `GitHubService.ensure_fresh` to return a
pre-populated tmp dir, then assert the adapter emits the expected
Service + CodeArtifact records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from core.adapters import IngestionContext
from ingestion.adapters.github import (
    GitHubAdapter,
    GitHubAdapterConfig,
    GitHubService,
    GitHubStore,
    RepoCloner,
)
from ingestion.adapters.github.service import FreshResult, normalize_repo_url

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


def _make_fake_clone(root: Path) -> Path:
    """Lay out a tiny repo on disk. No `.git` needed — the Walker only
    cares about the source files."""
    (root / "src").mkdir(parents=True)
    (root / "src" / "handlers.py").write_text(SAMPLE_HANDLER)
    return root


class _StubService(GitHubService):
    """A GitHubService that skips real git ops. Returns the pre-built
    clone path as if it had just been fetched."""

    def __init__(self, store: GitHubStore, cloner: RepoCloner, clone_path: Path) -> None:
        super().__init__(store=store, cloner=cloner)
        self._fake_path = clone_path
        self._ingested_at: dict[str, str] = {}

    def ensure_fresh(self, url: str, *, now: Any = None) -> FreshResult:  # type: ignore[override]
        canonical = normalize_repo_url(url)
        if self._store.get_repo(canonical) is None:
            self.add_repo(canonical)
        self._store.record_clone(canonical, sha="deadbeef")
        return FreshResult(
            path=self._fake_path,
            sha="deadbeef",
            was_stale=True,
            was_fresh_clone=True,
        )

    def is_ingested(self, url: str, sha: str) -> bool:  # type: ignore[override]
        return self._ingested_at.get(normalize_repo_url(url)) == sha

    def record_ingest(self, url: str, *, sha: str, at: Any = None) -> None:  # type: ignore[override]
        self._ingested_at[normalize_repo_url(url)] = sha
        super().record_ingest(url, sha=sha, at=at)


@pytest.fixture
def stub_service(tmp_path: Path) -> _StubService:
    clone = _make_fake_clone(tmp_path / "clone")
    store = GitHubStore(":memory:")
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    return _StubService(store=store, cloner=cloner, clone_path=clone)


def _config(tmp_path: Path) -> GitHubAdapterConfig:
    return GitHubAdapterConfig(
        clones_dir=tmp_path / "clones",
        store_path=":memory:",
    )


def test_emits_service_and_artifacts(tmp_path: Path, stub_service: _StubService) -> None:
    stub_service.add_repo("acme/payment-service")
    adapter = GitHubAdapter(_config(tmp_path), service=stub_service)
    result = adapter.extract(IngestionContext(now=NOW))

    assert len(result.services) == 1
    svc = result.services[0]
    assert svc.id == "acme/payment-service"
    assert svc.name == "payment-service"
    assert svc.language == "python"  # derived from .py dominance in the fake clone

    endpoint_artifacts = [a for a in result.artifacts if a.type == "endpoint"]
    function_artifacts = [a for a in result.artifacts if a.type == "function"]
    assert any(a.name == "POST /charges" for a in endpoint_artifacts)
    assert any(a.name == "compute_fee" for a in function_artifacts)
    assert all(not a.name.startswith("_") for a in function_artifacts)


def test_skips_when_sha_already_ingested(
    tmp_path: Path, stub_service: _StubService
) -> None:
    """Second pass with the same SHA should not produce records again —
    the adapter trusts the loader's idempotency but doesn't re-do the walk."""
    stub_service.add_repo("acme/payment-service")
    adapter = GitHubAdapter(_config(tmp_path), service=stub_service)

    first = adapter.extract(IngestionContext(now=NOW))
    assert len(first.services) == 1

    # Flip ensure_fresh to return was_stale=False this time.
    def _frozen_ensure(self: _StubService, url: str, *, now: Any = None) -> FreshResult:
        return FreshResult(
            path=stub_service._fake_path,  # noqa: SLF001
            sha="deadbeef",
            was_stale=False,
            was_fresh_clone=False,
        )

    stub_service.ensure_fresh = _frozen_ensure.__get__(stub_service)  # type: ignore[method-assign]
    second = adapter.extract(IngestionContext(now=NOW))
    assert second.services == []
    assert second.artifacts == []
    assert "skipped=1" in (second.coverage.notes if second.coverage else "")


def test_empty_registry_returns_zero_coverage(tmp_path: Path) -> None:
    store = GitHubStore(":memory:")
    cloner = RepoCloner(clones_dir=tmp_path / "clones")
    service = GitHubService(store=store, cloner=cloner)
    adapter = GitHubAdapter(_config(tmp_path), service=service)
    result = adapter.extract(IngestionContext(now=NOW))
    assert result.services == []
    assert result.artifacts == []
    assert result.coverage is not None
    assert result.coverage.services_total == 0


def test_context_repos_filter_limits_scope(
    tmp_path: Path, stub_service: _StubService
) -> None:
    stub_service.add_repo("acme/a")
    stub_service.add_repo("acme/b")
    adapter = GitHubAdapter(_config(tmp_path), service=stub_service)
    result = adapter.extract(
        IngestionContext(now=NOW, repos=("acme/a",))
    )
    assert {s.id for s in result.services} == {"acme/a"}
