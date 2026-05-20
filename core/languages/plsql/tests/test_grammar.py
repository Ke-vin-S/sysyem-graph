"""Tests for PlSqlGrammar."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.facts import FactKind
from core.languages.plsql.grammar import PlSqlGrammar


@pytest.fixture
def grammar() -> PlSqlGrammar:
    return PlSqlGrammar()


PKG_BODY = """\
CREATE OR REPLACE PACKAGE BODY pkg_billing AS
  PROCEDURE charge(p_id NUMBER) IS
  BEGIN
    pkg_audit.log('charging');
    UPDATE customer SET status = 'CHARGED' WHERE id = p_id;
    SELECT amount FROM invoice WHERE customer_id = p_id;
    INSERT INTO charge_log(customer_id) VALUES (p_id);
    DELETE FROM tmp_charges WHERE id = p_id;
  END charge;

  FUNCTION compute_fee(p_amount NUMBER) RETURN NUMBER IS
  BEGIN
    RETURN p_amount * 0.03;
  END compute_fee;
END pkg_billing;
"""


def test_emits_class_def_for_package(grammar: PlSqlGrammar) -> None:
    facts = grammar.extract(Path("pkg.pkb"), PKG_BODY, repo_id="r")
    packages = [f for f in facts if f.kind is FactKind.CLASS_DEF]
    assert len(packages) == 1
    assert packages[0].data["name"] == "pkg_billing"
    assert packages[0].data["kind"] == "package"


def test_emits_symbol_for_procedure_and_function(grammar: PlSqlGrammar) -> None:
    facts = grammar.extract(Path("pkg.pkb"), PKG_BODY, repo_id="r")
    symbols = [f for f in facts if f.kind is FactKind.SYMBOL]
    by_name = {f.data["name"]: f for f in symbols}
    assert "charge" in by_name
    assert by_name["charge"].data["sym_kind"] == "procedure"
    assert by_name["charge"].data["enclosing_package"] == "pkg_billing"
    assert "compute_fee" in by_name
    assert by_name["compute_fee"].data["sym_kind"] == "function"


def test_extracts_qualified_calls(grammar: PlSqlGrammar) -> None:
    facts = grammar.extract(Path("pkg.pkb"), PKG_BODY, repo_id="r")
    calls = [f for f in facts if f.kind is FactKind.CALL]
    callees = {f.data["callee"] for f in calls}
    assert "pkg_audit.log" in callees
    # Bare control keywords like `RETURN`, `BEGIN`, `END` must NOT appear.
    assert not any(c in {"return", "begin", "end"} for c in callees)


def test_procedure_declaration_is_not_a_call(grammar: PlSqlGrammar) -> None:
    """The regex `<ident>(` matches procedure declarations too; the
    declarator-filter should drop them."""
    spec = "PROCEDURE charge(p_id NUMBER);\n"
    facts = grammar.extract(Path("spec.pks"), spec, repo_id="r")
    calls = [f for f in facts if f.kind is FactKind.CALL]
    assert calls == []


def test_emits_sql_statement_for_dml(grammar: PlSqlGrammar) -> None:
    facts = grammar.extract(Path("pkg.pkb"), PKG_BODY, repo_id="r")
    sql = [f for f in facts if f.kind is FactKind.SQL_STATEMENT]
    by_op = {f.data["operation"]: f for f in sql}
    assert "select" in by_op
    assert by_op["select"].data["tables"] == ["invoice"]
    assert "update" in by_op
    assert by_op["update"].data["tables"] == ["customer"]
    assert "insert" in by_op
    assert by_op["insert"].data["tables"] == ["charge_log"]
    assert "delete" in by_op
    assert by_op["delete"].data["tables"] == ["tmp_charges"]


def test_emits_call_form_for_invoke(grammar: PlSqlGrammar) -> None:
    src = "BEGIN\n  CALL pkg_x.do_it(:v);\nEND;\n"
    facts = grammar.extract(Path("x.sql"), src, repo_id="r")
    sql = [f for f in facts if f.kind is FactKind.SQL_STATEMENT]
    calls = [f for f in sql if f.data["operation"] == "call"]
    assert len(calls) == 1
    assert calls[0].data["target_proc"] == "pkg_x.do_it"


def test_normalizes_identifiers_to_lowercase(grammar: PlSqlGrammar) -> None:
    src = "CREATE PACKAGE PKG_LOUD AS\nPROCEDURE LoudProc;\nEND;\n"
    facts = grammar.extract(Path("loud.pks"), src, repo_id="r")
    pkg = next(f for f in facts if f.kind is FactKind.CLASS_DEF)
    assert pkg.data["name"] == "pkg_loud"
    proc = next(
        f for f in facts
        if f.kind is FactKind.SYMBOL and f.data["sym_kind"] == "procedure"
    )
    assert proc.data["name"] == "loudproc"


def test_malformed_input_returns_empty(grammar: PlSqlGrammar) -> None:
    # Comments-only / nonsense — should still not raise.
    src = "-- just a comment\n/* block */"
    assert grammar.extract(Path("x.sql"), src, repo_id="r") == []


def test_comments_dont_create_false_symbols(grammar: PlSqlGrammar) -> None:
    src = "/* PROCEDURE pretend_proc IS BEGIN end; */\nBEGIN NULL; END;\n"
    facts = grammar.extract(Path("x.sql"), src, repo_id="r")
    assert not any(
        f.kind is FactKind.SYMBOL and f.data.get("name") == "pretend_proc"
        for f in facts
    )
