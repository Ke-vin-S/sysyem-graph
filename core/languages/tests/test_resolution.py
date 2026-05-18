"""Module-to-file resolution + reverse-mapping tests."""

from __future__ import annotations

from core.languages import load_library
from core.languages.library import DEFAULT_LANGUAGES_DIR
from core.languages.resolution import (
    init_file_to_module,
    is_aggregator_file,
    resolve_candidate_files,
)


def test_python_module_expands_to_two_candidates() -> None:
    lib = load_library(DEFAULT_LANGUAGES_DIR)
    python = lib.get("python")
    cands = resolve_candidate_files("core.resolvers", python)
    assert cands == ["core/resolvers.py", "core/resolvers/__init__.py"]


def test_java_module_expands_to_one_candidate() -> None:
    lib = load_library(DEFAULT_LANGUAGES_DIR)
    java = lib.get("java")
    assert resolve_candidate_files("com.example.Foo", java) == ["com/example/Foo.java"]


def test_plsql_module_expands_to_three_candidates() -> None:
    lib = load_library(DEFAULT_LANGUAGES_DIR)
    plsql = lib.get("plsql")
    cands = resolve_candidate_files("payments.charges", plsql)
    assert set(cands) == {
        "payments/charges.pks",
        "payments/charges.pkb",
        "payments/charges.sql",
    }


def test_empty_module_returns_empty() -> None:
    lib = load_library(DEFAULT_LANGUAGES_DIR)
    python = lib.get("python")
    assert resolve_candidate_files("", python) == []
    assert resolve_candidate_files(".", python) == []


def test_python_aggregator_recognizes_init_py() -> None:
    lib = load_library(DEFAULT_LANGUAGES_DIR)
    python = lib.get("python")
    assert is_aggregator_file("core/resolvers/__init__.py", python)
    assert not is_aggregator_file("core/resolvers/endpoint_resolver.py", python)


def test_plsql_aggregator_uses_wildcard() -> None:
    lib = load_library(DEFAULT_LANGUAGES_DIR)
    plsql = lib.get("plsql")
    assert is_aggregator_file("payments/charges.pks", plsql)
    assert not is_aggregator_file("payments/charges.pkb", plsql)


def test_java_has_no_aggregator() -> None:
    lib = load_library(DEFAULT_LANGUAGES_DIR)
    java = lib.get("java")
    assert not is_aggregator_file("com/x/Foo.java", java)


def test_init_file_to_module_prefers_longest_match() -> None:
    """`__init__.py` matches both `{module}.py` and `{module}/__init__.py`
    templates; the longer suffix wins so we get `core.x` not `core.x.__init__`."""
    lib = load_library(DEFAULT_LANGUAGES_DIR)
    python = lib.get("python")
    assert init_file_to_module("core/resolvers/__init__.py", python) == "core.resolvers"


def test_init_file_to_module_plsql_spec() -> None:
    lib = load_library(DEFAULT_LANGUAGES_DIR)
    plsql = lib.get("plsql")
    assert init_file_to_module("payments/charges.pks", plsql) == "payments.charges"


def test_init_file_to_module_returns_empty_for_unmatched() -> None:
    lib = load_library(DEFAULT_LANGUAGES_DIR)
    python = lib.get("python")
    assert init_file_to_module("README.md", python) == ""
