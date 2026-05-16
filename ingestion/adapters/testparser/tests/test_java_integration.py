"""End-to-end: the test parser adapter discovers and classifies .java tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.adapters import IngestionContext
from core.types import TestType
from ingestion.adapters.testparser import TestParserAdapter, TestParserAdapterConfig

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)

UNIT_JAVA = """\
package com.example;

import org.junit.jupiter.api.Test;

public class MathTest {
    @Test
    void testAddition() {
        int a = 1 + 1;
    }
}
"""

INTEGRATION_JAVA = """\
package com.example;

import org.junit.jupiter.api.Test;
import okhttp3.OkHttpClient;

public class ApiIT {
    @Test
    void testApi() throws Exception {
        new OkHttpClient().newCall(null).execute();
    }
}
"""


def test_adapter_picks_up_java_files(tmp_path: Path) -> None:
    repo = tmp_path / "payment-service" / "src" / "test" / "java" / "com" / "example"
    repo.mkdir(parents=True)
    (repo / "MathTest.java").write_text(UNIT_JAVA)
    (repo / "ApiIT.java").write_text(INTEGRATION_JAVA)

    config = TestParserAdapterConfig(root=tmp_path)
    adapter = TestParserAdapter(config)
    result = adapter.extract(IngestionContext(now=NOW))

    by_name = {t.name: t for t in result.tests}
    assert "testAddition" in by_name
    assert "testApi" in by_name
    assert by_name["testAddition"].type is TestType.UNIT
    assert by_name["testApi"].type is TestType.INTEGRATION
    assert by_name["testAddition"].repo_id == "payment-service"


def test_adapter_skips_non_test_java_files(tmp_path: Path) -> None:
    # A bare .java file that doesn't match a test naming convention
    # should be ignored even if it lives in the repo.
    repo = tmp_path / "auth-service"
    repo.mkdir()
    (repo / "Helper.java").write_text("package com.example; public class Helper {}")

    config = TestParserAdapterConfig(root=tmp_path)
    adapter = TestParserAdapter(config)
    result = adapter.extract(IngestionContext(now=NOW))
    assert result.tests == []
