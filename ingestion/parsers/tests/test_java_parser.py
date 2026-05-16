"""Tests for the Java parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.parsers import JavaParser


@pytest.fixture
def java_parser() -> JavaParser:
    return JavaParser()


UNIT_TEST = """\
package com.example;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class MathTest {
    @Test
    void testAddition() {
        assertEquals(2, 1 + 1);
    }
}
"""

INTEGRATION_TEST = """\
package com.example;

import org.junit.jupiter.api.Test;
import okhttp3.OkHttpClient;
import okhttp3.Request;

public class ApiIntegrationTest {
    @Test
    void testCallsApi() throws Exception {
        OkHttpClient client = new OkHttpClient();
        client.newCall(new Request.Builder().url("http://x").build()).execute();
    }
}
"""

MOCKED_TEST = """\
package com.example;

import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import okhttp3.OkHttpClient;

public class MockedTest {
    @Mock private OkHttpClient client;

    @Test
    void testWithMock() {
        // client is mocked at the field level
        assert client == null || client != null;
    }
}
"""

PARAMETERIZED_TEST = """\
package com.example;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

public class ParamTest {
    @ParameterizedTest
    @ValueSource(ints = {1, 2, 3})
    void testWithValues(int value) {
        assert value > 0;
    }
}
"""

NON_TEST_FILE = """\
package com.example;

public class Helper {
    public int compute(int x) {
        return x * 2;
    }
}
"""

JUNIT4_TEST = """\
package com.example;

import org.junit.Test;

public class LegacyTest {
    @Test
    public void testLegacyStyle() {
        // junit 4 style
    }
}
"""


def test_finds_test_method(java_parser: JavaParser) -> None:
    parsed = java_parser.parse(Path("MathTest.java"), UNIT_TEST)
    assert [p.name for p in parsed] == ["testAddition"]
    assert parsed[0].decorators == ("Test",)
    assert parsed[0].line_start > 1


def test_collects_imports(java_parser: JavaParser) -> None:
    parsed = java_parser.parse(Path("MathTest.java"), UNIT_TEST)
    assert "org.junit.jupiter.api.Test" in parsed[0].imports


def test_flags_external_call(java_parser: JavaParser) -> None:
    parsed = java_parser.parse(Path("ApiIntegrationTest.java"), INTEGRATION_TEST)
    assert parsed[0].calls_external is True


def test_respects_field_mock(java_parser: JavaParser) -> None:
    parsed = java_parser.parse(Path("MockedTest.java"), MOCKED_TEST)
    assert "OkHttpClient" in parsed[0].mocked_modules
    # The class is mocked at field level so it shouldn't be reported as a
    # real external call.
    assert parsed[0].calls_external is False


def test_recognizes_parameterized(java_parser: JavaParser) -> None:
    parsed = java_parser.parse(Path("ParamTest.java"), PARAMETERIZED_TEST)
    assert [p.name for p in parsed] == ["testWithValues"]
    assert "ParameterizedTest" in parsed[0].decorators


def test_skips_non_test_class(java_parser: JavaParser) -> None:
    parsed = java_parser.parse(Path("Helper.java"), NON_TEST_FILE)
    assert parsed == []


def test_recognizes_junit4(java_parser: JavaParser) -> None:
    parsed = java_parser.parse(Path("LegacyTest.java"), JUNIT4_TEST)
    assert [p.name for p in parsed] == ["testLegacyStyle"]


def test_syntax_error_returns_empty(java_parser: JavaParser) -> None:
    parsed = java_parser.parse(Path("Broken.java"), "this is not java {{{")
    assert parsed == []


def test_method_without_test_annotation_is_skipped(java_parser: JavaParser) -> None:
    src = """\
package com.example;
public class Foo {
    public void testHelper() { /* no annotation, not a real test */ }
}
"""
    assert java_parser.parse(Path("Foo.java"), src) == []
