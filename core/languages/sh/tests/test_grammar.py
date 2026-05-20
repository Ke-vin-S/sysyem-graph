"""Tests for ShGrammar."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.facts import FactKind
from core.languages.sh.grammar import ShGrammar


@pytest.fixture
def grammar() -> ShGrammar:
    return ShGrammar()


SAMPLE = """\
#!/bin/sh
# Run the billing job.
BIN_DIR=/opt/billing/bin

run_daily() {
    ${BIN_DIR}/charge_loader prod || exit 1
    ./scripts/load_invoices.sh
    sqlplus user/pass@prod @sql/cleanup.sql
}

run_daily
"""


def test_emits_function_definition(grammar: ShGrammar) -> None:
    facts = grammar.extract(Path("run.sh"), SAMPLE, repo_id="r")
    symbols = [f for f in facts if f.kind is FactKind.SYMBOL]
    assert any(f.data["name"] == "run_daily" for f in symbols)


def test_extracts_command_calls_including_paths(grammar: ShGrammar) -> None:
    facts = grammar.extract(Path("run.sh"), SAMPLE, repo_id="r")
    callees = {f.data["callee"] for f in facts if f.kind is FactKind.CALL}
    # Variable-prefixed binary
    assert "${BIN_DIR}/charge_loader" in callees
    # Relative-path script call
    assert "./scripts/load_invoices.sh" in callees
    # Bare command
    assert "sqlplus" in callees
    # And the user-defined function gets called at the bottom
    assert "run_daily" in callees


def test_keywords_are_not_calls(grammar: ShGrammar) -> None:
    src = "if true; then\n  echo hi\nfi\nfor x in 1 2; do echo $x; done\n"
    facts = grammar.extract(Path("x.sh"), src, repo_id="r")
    callees = {f.data["callee"] for f in facts if f.kind is FactKind.CALL}
    assert "if" not in callees
    assert "for" not in callees
    assert "done" not in callees
    assert "echo" not in callees


def test_sqlplus_extracts_script_path(grammar: ShGrammar) -> None:
    facts = grammar.extract(Path("run.sh"), SAMPLE, repo_id="r")
    sql = [f for f in facts if f.kind is FactKind.SQL_STATEMENT]
    assert len(sql) == 1
    assert sql[0].data["operation"] == "script"
    assert sql[0].data["target_proc"] == "sql/cleanup.sql"


def test_comments_dont_create_false_calls(grammar: ShGrammar) -> None:
    src = "# fake_command arg1 arg2\necho real\n"
    facts = grammar.extract(Path("x.sh"), src, repo_id="r")
    callees = {f.data["callee"] for f in facts if f.kind is FactKind.CALL}
    assert "fake_command" not in callees


def test_variable_assignments_not_captured_as_calls(grammar: ShGrammar) -> None:
    src = "FOO=bar\nBAZ=quux\n./binary $FOO\n"
    facts = grammar.extract(Path("x.sh"), src, repo_id="r")
    callees = {f.data["callee"] for f in facts if f.kind is FactKind.CALL}
    assert "FOO" not in callees
    assert "BAZ" not in callees
    assert "./binary" in callees
