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
