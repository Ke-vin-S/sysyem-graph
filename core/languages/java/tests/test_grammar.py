"""JavaGrammar -> Fact tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.facts import FactKind
from core.languages.java.grammar import JavaGrammar

CONTROLLER = """\
package com.example;

import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

@RestController
@RequestMapping("/users")
public class UserController {
    @GetMapping("/{id}")
    public String getUser(@PathVariable String id) {
        return "ok";
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
    void testNothing() {
    }
}
"""


@pytest.fixture
def grammar() -> JavaGrammar:
    return JavaGrammar()


def test_controller_class_annotations(grammar: JavaGrammar) -> None:
    facts = grammar.extract(Path("UserController.java"), CONTROLLER, repo_id="r")
    class_facts = [f for f in facts if f.kind is FactKind.CLASS_DEF]
    assert [c.data["name"] for c in class_facts] == ["UserController"]

    annotations = [f for f in facts if f.kind is FactKind.ANNOTATION]
    class_annotations = {a.data["callee"] for a in annotations if a.data["target_kind"] == "class"}
    assert {"RestController", "RequestMapping"} <= class_annotations

    request_mapping = next(a for a in annotations if a.data["callee"] == "RequestMapping")
    # Single positional literal argument carrying the path prefix.
    assert request_mapping.data["args"] == ["/users"]


def test_controller_method_annotations(grammar: JavaGrammar) -> None:
    facts = grammar.extract(Path("UserController.java"), CONTROLLER, repo_id="r")
    get_mapping = next(
        a for a in facts
        if a.kind is FactKind.ANNOTATION and a.data["callee"] == "GetMapping"
    )
    assert get_mapping.data["target_symbol"] == "getUser"
    assert get_mapping.data["args"] == ["/{id}"]


def test_mocked_field_annotation(grammar: JavaGrammar) -> None:
    facts = grammar.extract(Path("MockedTest.java"), MOCKED_TEST, repo_id="r")
    mock_ann = next(
        a for a in facts
        if a.kind is FactKind.ANNOTATION and a.data["callee"] == "Mock"
    )
    assert mock_ann.data["target_type"] == "OkHttpClient"
    assert mock_ann.data["target_kind"] == "field"


def test_imports_collected(grammar: JavaGrammar) -> None:
    facts = grammar.extract(Path("UserController.java"), CONTROLLER, repo_id="r")
    imports = {f.data["module"] for f in facts if f.kind is FactKind.IMPORT}
    assert "org.springframework.web.bind.annotation.RestController" in imports


def test_syntax_error_returns_empty(grammar: JavaGrammar) -> None:
    assert grammar.extract(Path("bad.java"), "this { is not :: java", repo_id="r") == []
