"""Tests for TestResolver: facts + framework YAML -> TestCase records.

These tests parallel the previous Python/Java classifier tests but now drive
classification entirely from the shipped framework YAMLs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.frameworks import compose, detect_frameworks, load_library
from core.frameworks.library import DEFAULT_FRAMEWORKS_DIR
from core.resolvers import ResolverContext, TestResolver
from core.types import TestType
from core.walker import Walker


@pytest.fixture(scope="module")
def library():
    return load_library(DEFAULT_FRAMEWORKS_DIR)


def _resolve_tests(tmp_path: Path, library, *, repo_id: str = "auth-service"):
    repo = tmp_path / repo_id
    walker = Walker()
    tree = walker.walk(repo, repo_id=repo_id)
    detected = detect_frameworks(tree, library)
    effective = tuple(compose(fw, None) for fw in detected)
    return TestResolver().resolve(ResolverContext(tree=tree, frameworks=effective, repo_id=repo_id))


def _seed(root: Path, files: dict[str, str]) -> None:
    for relpath, content in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_unit_classification(tmp_path: Path, library) -> None:
    _seed(
        tmp_path,
        {
            "auth-service/tests/test_x.py": "def test_addition():\n    assert 1 + 1 == 2\n",
        },
    )
    tests = _resolve_tests(tmp_path, library)
    assert any(t.name == "test_addition" and t.type is TestType.UNIT for t in tests)


def test_integration_when_unmocked_external(tmp_path: Path, library) -> None:
    _seed(
        tmp_path,
        {
            "auth-service/tests/test_x.py": (
                "import httpx\n"
                "def test_calls():\n"
                "    httpx.get('http://x')\n"
            ),
        },
    )
    tests = _resolve_tests(tmp_path, library)
    target = next(t for t in tests if t.name == "test_calls")
    assert target.type is TestType.INTEGRATION


def test_unit_when_external_is_mocked(tmp_path: Path, library) -> None:
    _seed(
        tmp_path,
        {
            "auth-service/tests/test_x.py": (
                "import httpx\n"
                "from unittest.mock import patch\n"
                "@patch('httpx.get')\n"
                "def test_mocked(mock_get):\n"
                "    pass\n"
            ),
        },
    )
    tests = _resolve_tests(tmp_path, library)
    target = next(t for t in tests if t.name == "test_mocked")
    assert target.type is TestType.UNIT


def test_integration_marker_path(tmp_path: Path, library) -> None:
    _seed(
        tmp_path,
        {
            "auth-service/tests/integration/test_x.py": (
                "def test_thing():\n    assert True\n"
            ),
        },
    )
    tests = _resolve_tests(tmp_path, library)
    target = next(t for t in tests if t.name == "test_thing")
    assert target.type is TestType.INTEGRATION


def test_e2e_marker_decorator(tmp_path: Path, library) -> None:
    _seed(
        tmp_path,
        {
            "auth-service/tests/test_x.py": (
                "import pytest\n"
                "@pytest.mark.e2e\n"
                "def test_e2e():\n"
                "    pass\n"
            ),
        },
    )
    tests = _resolve_tests(tmp_path, library)
    target = next(t for t in tests if t.name == "test_e2e")
    assert target.type is TestType.E2E


def test_junit_test_picked_up(tmp_path: Path, library) -> None:
    _seed(
        tmp_path,
        {
            "payment-service/src/test/java/com/example/MathTest.java": (
                "package com.example;\n"
                "import org.junit.jupiter.api.Test;\n"
                "public class MathTest {\n"
                "  @Test\n"
                "  void testAddition() { }\n"
                "}\n"
            ),
        },
    )
    tests = _resolve_tests(tmp_path, library, repo_id="payment-service")
    target = next(t for t in tests if t.name == "testAddition")
    assert target.type is TestType.UNIT


def test_java_mocked_external_stays_unit(tmp_path: Path, library) -> None:
    _seed(
        tmp_path,
        {
            "payment-service/src/test/java/com/example/MockedTest.java": (
                "package com.example;\n"
                "import org.junit.jupiter.api.Test;\n"
                "import org.mockito.Mock;\n"
                "import okhttp3.OkHttpClient;\n"
                "public class MockedTest {\n"
                "  @Mock private OkHttpClient client;\n"
                "  @Test void testWithMock() { }\n"
                "}\n"
            ),
        },
    )
    tests = _resolve_tests(tmp_path, library, repo_id="payment-service")
    target = next(t for t in tests if t.name == "testWithMock")
    assert target.type is TestType.UNIT


def test_emissions_carry_provenance(tmp_path: Path, library) -> None:
    _seed(
        tmp_path,
        {
            "auth-service/tests/test_x.py": "def test_addition():\n    assert 1 + 1 == 2\n",
        },
    )
    tests = _resolve_tests(tmp_path, library)
    target = next(t for t in tests if t.name == "test_addition")
    assert target.produced_by == "test_resolver"
    assert len(target.from_facts) >= 1
    assert all(fid.startswith("fact:") for fid in target.from_facts)
