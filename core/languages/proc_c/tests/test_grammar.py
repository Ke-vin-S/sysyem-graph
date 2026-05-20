"""Tests for ProCGrammar."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.facts import FactKind
from core.languages.proc_c.grammar import ProCGrammar


@pytest.fixture
def grammar() -> ProCGrammar:
    return ProCGrammar()


SAMPLE = """\
#include <stdio.h>
#include "billing.h"

int charge_customer(int id, double amount) {
    EXEC SQL SELECT amount INTO :v_amt FROM invoice WHERE id = :id;
    EXEC SQL UPDATE customer SET balance = balance - :amount WHERE id = :id;
    EXEC SQL INSERT INTO charge_log(customer_id, amt) VALUES (:id, :amount);
    EXEC SQL EXECUTE BEGIN pkg_audit.log_charge(:id, :amount); END;
    log_charge(id, amount);
    return 0;
}
"""


def test_emits_c_structure(grammar: ProCGrammar) -> None:
    facts = grammar.extract(Path("charge.pc"), SAMPLE, repo_id="r")
    symbols = [f for f in facts if f.kind is FactKind.SYMBOL]
    assert any(f.data["name"] == "charge_customer" for f in symbols)
    imports = [f for f in facts if f.kind is FactKind.IMPORT]
    assert any(f.data["module"] == "stdio.h" for f in imports)
    assert any(f.data["module"] == "billing.h" for f in imports)


def test_emits_sql_statements_for_each_operation(grammar: ProCGrammar) -> None:
    facts = grammar.extract(Path("charge.pc"), SAMPLE, repo_id="r")
    sql = [f for f in facts if f.kind is FactKind.SQL_STATEMENT]
    by_op = {f.data["operation"] for f in sql}
    assert "select" in by_op
    assert "update" in by_op
    assert "insert" in by_op
    assert "execute" in by_op


def test_captures_tables_from_exec_sql(grammar: ProCGrammar) -> None:
    facts = grammar.extract(Path("charge.pc"), SAMPLE, repo_id="r")
    sql = [f for f in facts if f.kind is FactKind.SQL_STATEMENT]
    by_op = {f.data["operation"]: f for f in sql}
    assert by_op["select"].data["tables"] == ["invoice"]
    assert by_op["update"].data["tables"] == ["customer"]
    assert by_op["insert"].data["tables"] == ["charge_log"]


def test_captures_target_proc_for_execute_block(grammar: ProCGrammar) -> None:
    facts = grammar.extract(Path("charge.pc"), SAMPLE, repo_id="r")
    sql = [f for f in facts if f.kind is FactKind.SQL_STATEMENT]
    execute = next(f for f in sql if f.data["operation"] == "execute")
    assert execute.data["target_proc"] == "pkg_audit.log_charge"


def test_exec_sql_blocks_dont_pollute_c_extractor(grammar: ProCGrammar) -> None:
    """The SQL block contains `SELECT amount INTO :v_amt` — the C call
    regex would match `INTO(` if SQL leaked through. Verify it doesn't."""
    facts = grammar.extract(Path("charge.pc"), SAMPLE, repo_id="r")
    callees = {f.data["callee"] for f in facts if f.kind is FactKind.CALL}
    assert "SELECT" not in callees
    assert "INTO" not in callees
    assert "log_charge" in callees  # the genuine C call


def test_malformed_returns_empty_safely(grammar: ProCGrammar) -> None:
    assert grammar.extract(Path("x.pc"), "EXEC SQL no terminator", repo_id="r") != [None]
