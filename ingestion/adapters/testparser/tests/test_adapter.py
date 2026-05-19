"""End-to-end tests for the test parser adapter.

The internal classifier/parser/coverage classes were absorbed into grammars
and resolvers in Phase 1.5; these tests now exercise the adapter's public
output (TestCase records) which is the migration contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.adapters import IngestionContext
from core.types import TestType
from core.types.errors import IngestionError
from ingestion.adapters.testparser import TestParserAdapter, TestParserAdapterConfig

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)

UNIT_TEST = """\
def test_addition():
    assert 1 + 1 == 2
"""

INTEGRATION_TEST = """\
import httpx


def test_calls_api():
    httpx.get("http://example.com")
"""

MOCKED_TEST = """\
from unittest.mock import patch
import httpx


@patch("httpx.get")
def test_with_mock(mock_get):
    assert httpx is not None
"""


def test_adapter_walks_repos(tmp_path: Path) -> None:
    auth = tmp_path / "auth-service" / "tests"
    auth.mkdir(parents=True)
    (auth / "test_unit.py").write_text(UNIT_TEST)
    (auth / "test_integration.py").write_text(INTEGRATION_TEST)

    payment = tmp_path / "payment-service" / "tests"
    payment.mkdir(parents=True)
    (payment / "test_mock.py").write_text(MOCKED_TEST)

    config = TestParserAdapterConfig(root=tmp_path)
    adapter = TestParserAdapter(config)
    result = adapter.extract(IngestionContext(now=NOW))

    by_name = {t.name: t for t in result.tests}
    assert by_name["test_addition"].type is TestType.UNIT
    assert by_name["test_addition"].repo_id == "auth-service"
    assert by_name["test_calls_api"].type is TestType.INTEGRATION
    assert by_name["test_with_mock"].type is TestType.UNIT  # mocked httpx == unit


def test_adapter_raises_when_root_missing(tmp_path: Path) -> None:
    config = TestParserAdapterConfig(root=tmp_path / "does-not-exist")
    adapter = TestParserAdapter(config)
    with pytest.raises(IngestionError):
        adapter.extract(IngestionContext(now=NOW))


def test_adapter_integration_marker_path(tmp_path: Path) -> None:
    """Tests under `tests/integration/` should classify as INTEGRATION even
    when there's no unmocked external library call."""
    repo = tmp_path / "auth-service" / "tests" / "integration"
    repo.mkdir(parents=True)
    (repo / "test_thing.py").write_text("def test_thing():\n    assert True\n")
    adapter = TestParserAdapter(TestParserAdapterConfig(root=tmp_path))
    result = adapter.extract(IngestionContext(now=NOW))
    target = next(t for t in result.tests if t.name == "test_thing")
    assert target.type is TestType.INTEGRATION


def test_adapter_e2e_decorator(tmp_path: Path) -> None:
    repo = tmp_path / "auth-service" / "tests"
    repo.mkdir(parents=True)
    (repo / "test_x.py").write_text(
        "import pytest\n@pytest.mark.e2e\ndef test_e2e():\n    pass\n"
    )
    adapter = TestParserAdapter(TestParserAdapterConfig(root=tmp_path))
    result = adapter.extract(IngestionContext(now=NOW))
    target = next(t for t in result.tests if t.name == "test_e2e")
    assert target.type is TestType.E2E


# ---- single-repo mode (auto-detect + explicit override) -------------------


def test_adapter_treats_root_as_single_repo_when_pyproject_present(
    tmp_path: Path,
) -> None:
    """Pointing at a folder that IS a repo (has pyproject.toml) must NOT
    scan its src/, tests/, etc. as separate repos. The repo_id should be
    the folder's own name."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='foo'\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_unit.py").write_text(UNIT_TEST)

    config = TestParserAdapterConfig(root=tmp_path)
    adapter = TestParserAdapter(config)
    result = adapter.extract(IngestionContext(now=NOW))

    # Every emitted test belongs to one repo — the root folder itself.
    repo_ids = {t.repo_id for t in result.tests}
    assert repo_ids == {tmp_path.name}, repo_ids


def test_adapter_auto_detects_git_dir(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()  # bare presence is enough
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_unit.py").write_text(UNIT_TEST)

    adapter = TestParserAdapter(TestParserAdapterConfig(root=tmp_path))
    result = adapter.extract(IngestionContext(now=NOW))
    assert {t.repo_id for t in result.tests} == {tmp_path.name}


def test_adapter_parent_of_repos_when_no_markers(tmp_path: Path) -> None:
    """Existing behavior: no markers at root → each subdirectory is its
    own repo. Keeps the demo `./data/` workflow working."""
    a = tmp_path / "service-a" / "tests"
    a.mkdir(parents=True)
    (a / "test_unit.py").write_text(UNIT_TEST)
    b = tmp_path / "service-b" / "tests"
    b.mkdir(parents=True)
    (b / "test_unit.py").write_text(UNIT_TEST)

    adapter = TestParserAdapter(TestParserAdapterConfig(root=tmp_path))
    result = adapter.extract(IngestionContext(now=NOW))
    assert {t.repo_id for t in result.tests} == {"service-a", "service-b"}


def test_adapter_explicit_single_repo_override(tmp_path: Path) -> None:
    """Explicit override: single_repo=True forces single-repo mode even
    without markers."""
    a = tmp_path / "src"
    a.mkdir()
    (a / "test_unit.py").write_text(UNIT_TEST)

    adapter = TestParserAdapter(TestParserAdapterConfig(root=tmp_path, single_repo=True))
    result = adapter.extract(IngestionContext(now=NOW))
    assert {t.repo_id for t in result.tests} == {tmp_path.name}


def test_adapter_explicit_parent_of_repos_override(tmp_path: Path) -> None:
    """Explicit override: single_repo=False forces subdirectory scanning
    even when markers are present at the root."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='monorepo'\n")
    a = tmp_path / "service-a" / "tests"
    a.mkdir(parents=True)
    (a / "test_unit.py").write_text(UNIT_TEST)

    adapter = TestParserAdapter(
        TestParserAdapterConfig(root=tmp_path, single_repo=False)
    )
    result = adapter.extract(IngestionContext(now=NOW))
    # NOT the root folder; the subdirectory.
    assert {t.repo_id for t in result.tests} == {"service-a"}
