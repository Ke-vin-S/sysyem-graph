"""Tests for FormsGrammar — the binary-file stub."""

from __future__ import annotations

from pathlib import Path

from core.facts import FactKind
from core.languages.oracle_forms.grammar import FormsGrammar


def test_emits_one_form_app_symbol_per_file() -> None:
    facts = FormsGrammar().extract(
        Path("forms/billing_dashboard.fmb"), "garbage\x00binary", repo_id="r"
    )
    assert len(facts) == 1
    f = facts[0]
    assert f.kind is FactKind.SYMBOL
    assert f.data["sym_kind"] == "form_app"
    assert f.data["name"] == "billing_dashboard"


def test_fmx_files_supported() -> None:
    facts = FormsGrammar().extract(
        Path("orders.fmx"), "", repo_id="r"
    )
    assert facts[0].data["sym_kind"] == "form_app"
    assert facts[0].data["name"] == "orders"


def test_binary_content_does_not_crash() -> None:
    # Any kind of content (binary noise, empty, multi-line) should be fine.
    weird = "\x00\xff" * 1000 + "PROCEDURE looks_like_plsql_but_isnt"
    facts = FormsGrammar().extract(Path("x.fmb"), weird, repo_id="r")
    assert len(facts) == 1
