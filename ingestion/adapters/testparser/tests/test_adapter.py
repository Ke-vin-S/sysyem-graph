"""Tests for the test parser adapter: AST extraction, classification, walk."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.adapters import IngestionContext
from core.types import TestType
from ingestion.adapters.testparser import (
    TestClassifier,
    TestParserAdapter,
    TestParserAdapterConfig,
)
from ingestion.parsers import PythonParser
from ingestion.parsers.parser import ParsedTest

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


@pytest.fixture
def python_parser() -> PythonParser:
    return PythonParser()


def test_python_parser_finds_test_functions(tmp_path: Path, python_parser: PythonParser) -> None:
    file = tmp_path / "test_x.py"
    file.write_text(UNIT_TEST)
    parsed = python_parser.parse(file, UNIT_TEST)
    assert [p.name for p in parsed] == ["test_addition"]


def test_python_parser_flags_external_call(tmp_path: Path, python_parser: PythonParser) -> None:
    file = tmp_path / "test_x.py"
    parsed = python_parser.parse(file, INTEGRATION_TEST)
    assert parsed[0].calls_external is True
    assert "httpx" in parsed[0].imports


def test_python_parser_respects_mock(tmp_path: Path, python_parser: PythonParser) -> None:
    file = tmp_path / "test_x.py"
    parsed = python_parser.parse(file, MOCKED_TEST)
    assert "httpx" in parsed[0].mocked_modules
    # Mocked external library should not count as a real external call.
    assert parsed[0].calls_external is False


def test_classifier_unit_when_no_external() -> None:
    parsed = ParsedTest(name="test_x", file="t.py", line_start=1, line_end=1)
    assert TestClassifier().classify(parsed).type is TestType.UNIT


def test_classifier_integration_when_external_called() -> None:
    parsed = ParsedTest(
        name="test_x",
        file="tests/test_x.py",
        line_start=1,
        line_end=1,
        imports=("httpx",),
        calls_external=True,
    )
    assert TestClassifier().classify(parsed).type is TestType.INTEGRATION


def test_classifier_integration_by_path() -> None:
    parsed = ParsedTest(name="test_x", file="tests/integration/test_x.py", line_start=1, line_end=1)
    assert TestClassifier().classify(parsed).type is TestType.INTEGRATION


def test_classifier_e2e_marker() -> None:
    parsed = ParsedTest(
        name="test_x",
        file="t.py",
        line_start=1,
        line_end=1,
        decorators=("pytest.mark.e2e",),
    )
    assert TestClassifier().classify(parsed).type is TestType.E2E


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
    from core.types.errors import IngestionError

    with pytest.raises(IngestionError):
        adapter.extract(IngestionContext(now=NOW))
