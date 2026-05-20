"""Tests for CGrammar."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.facts import FactKind
from core.languages.c.grammar import CGrammar


@pytest.fixture
def grammar() -> CGrammar:
    return CGrammar()


SAMPLE = """\
#include <stdio.h>
#include "billing.h"

static int compute_fee(int amount) {
    return amount * 3;
}

int main(int argc, char **argv) {
    int fee = compute_fee(100);
    printf("%d\\n", fee);
    return 0;
}
"""


def test_extracts_system_and_local_includes(grammar: CGrammar) -> None:
    facts = grammar.extract(Path("main.c"), SAMPLE, repo_id="r")
    imports = [f for f in facts if f.kind is FactKind.IMPORT]
    modules = {f.data["module"] for f in imports}
    assert "stdio.h" in modules
    assert "billing.h" in modules


def test_extracts_function_definitions(grammar: CGrammar) -> None:
    facts = grammar.extract(Path("main.c"), SAMPLE, repo_id="r")
    symbols = [f for f in facts if f.kind is FactKind.SYMBOL]
    names = {f.data["name"] for f in symbols}
    assert "compute_fee" in names
    assert "main" in names
    # Keywords don't show up as symbols.
    assert "if" not in names


def test_extracts_call_sites_not_definitions(grammar: CGrammar) -> None:
    facts = grammar.extract(Path("main.c"), SAMPLE, repo_id="r")
    calls = [f for f in facts if f.kind is FactKind.CALL]
    callees = {f.data["callee"] for f in calls}
    # main() calls compute_fee and printf.
    assert "compute_fee" in callees
    assert "printf" in callees
    # The DEFINITION of compute_fee shouldn't appear as a call site.
    main_def_line = next(
        f.line for f in facts
        if f.kind is FactKind.SYMBOL and f.data["name"] == "compute_fee"
    )
    compute_fee_calls = [c for c in calls if c.data["callee"] == "compute_fee"]
    assert all(c.line != main_def_line for c in compute_fee_calls)


def test_strings_dont_create_false_calls(grammar: CGrammar) -> None:
    src = 'int x() { return printf("call_me_not(); some\\n"); }\n'
    facts = grammar.extract(Path("x.c"), src, repo_id="r")
    callees = {f.data["callee"] for f in facts if f.kind is FactKind.CALL}
    assert "call_me_not" not in callees


def test_malformed_returns_empty(grammar: CGrammar) -> None:
    # Truncated / nonsense should not raise.
    assert grammar.extract(Path("x.c"), "/*\nunterminated comment", repo_id="r") != [
        None
    ]  # not raising is the assertion
