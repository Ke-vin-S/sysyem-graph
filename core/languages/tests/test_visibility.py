"""Visibility predicate tests — one case per named rule."""

from __future__ import annotations

from core.facts import Fact, FactKind
from core.languages import is_public, registered_rules


def _symbol(name: str, *, file: str = "x.py", **data) -> Fact:
    return Fact(
        kind=FactKind.SYMBOL,
        file=file,
        line=1,
        repo_id="r",
        data={"sym_kind": "function", "name": name, **data},
    )


def test_registered_rules_include_all_expected() -> None:
    assert set(registered_rules()) >= {
        "leading_underscore_is_private",
        "java_public_modifier",
        "starts_uppercase",
        "plsql_declared_in_spec",
        "export_keyword",
        "always_public",
    }


def test_leading_underscore_is_private() -> None:
    assert is_public("leading_underscore_is_private", _symbol("compute"))
    assert not is_public("leading_underscore_is_private", _symbol("_internal"))
    assert not is_public("leading_underscore_is_private", _symbol(""))


def test_java_public_modifier_requires_public() -> None:
    assert is_public("java_public_modifier", _symbol("getX", modifiers=["public"]))
    assert not is_public("java_public_modifier", _symbol("getX", modifiers=["private"]))
    # No modifier list => package-private => not part of the public surface.
    assert not is_public("java_public_modifier", _symbol("getX"))


def test_starts_uppercase_for_go_style() -> None:
    assert is_public("starts_uppercase", _symbol("Exported"))
    assert not is_public("starts_uppercase", _symbol("internal"))
    assert not is_public("starts_uppercase", _symbol(""))


def test_plsql_declared_in_spec_uses_file_extension() -> None:
    spec = _symbol("create_charge", file="payments/charges.pks")
    body = _symbol("create_charge", file="payments/charges.pkb")
    assert is_public("plsql_declared_in_spec", spec)
    assert not is_public("plsql_declared_in_spec", body)


def test_export_keyword_uses_exported_flag() -> None:
    assert is_public("export_keyword", _symbol("Foo", exported=True))
    assert not is_public("export_keyword", _symbol("Foo", exported=False))
    assert not is_public("export_keyword", _symbol("Foo"))


def test_always_public_returns_true() -> None:
    assert is_public("always_public", _symbol("x"))


def test_unknown_rule_defaults_to_public() -> None:
    assert is_public("not_a_real_rule", _symbol("x"))
