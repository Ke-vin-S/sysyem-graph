"""LanguageLibrary loader tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.languages import GrammarKind, LanguageLibrary, load_library
from core.languages.library import DEFAULT_LANGUAGES_DIR
from core.types.errors import ConfigurationError


def test_load_library_picks_up_shipped_yamls() -> None:
    library = load_library(DEFAULT_LANGUAGES_DIR)
    assert set(library.names()) == {"python", "java", "plsql"}


def test_python_profile_shape() -> None:
    library = load_library(DEFAULT_LANGUAGES_DIR)
    python = library.get("python")
    assert python.file_extensions == (".py",)
    assert python.grammar.kind is GrammarKind.NATIVE
    assert "PythonGrammar" in python.grammar.driver
    assert python.module_resolution.separator == "."
    assert "{module}.py" in python.module_resolution.candidate_path_templates
    assert "{module}/__init__.py" in python.module_resolution.candidate_path_templates
    assert "__init__.py" in python.package_aggregator.files
    assert python.visibility.rule == "leading_underscore_is_private"
    assert "test_" in python.test_paths.function_name_prefixes


def test_java_profile_shape() -> None:
    library = load_library(DEFAULT_LANGUAGES_DIR)
    java = library.get("java")
    assert java.file_extensions == (".java",)
    assert java.grammar.kind is GrammarKind.NATIVE
    assert java.package_aggregator.files == ()
    assert java.visibility.rule == "java_public_modifier"


def test_plsql_profile_uses_llm_grammar() -> None:
    library = load_library(DEFAULT_LANGUAGES_DIR)
    plsql = library.get("plsql")
    assert plsql.grammar.kind is GrammarKind.LLM
    assert plsql.grammar.driver == ""  # no native driver
    assert set(plsql.file_extensions) >= {".sql", ".pks", ".pkb"}


def test_for_file_dispatches_by_extension() -> None:
    library = load_library(DEFAULT_LANGUAGES_DIR)
    assert library.for_file("a/b/c.py").name == "python"
    assert library.for_file("Foo.java").name == "java"
    assert library.for_file("charges.pks").name == "plsql"
    assert library.for_file("README.md") is None


def test_for_extension_lookup() -> None:
    library = load_library(DEFAULT_LANGUAGES_DIR)
    assert library.for_extension(".py").name == "python"
    assert library.for_extension(".pkb").name == "plsql"
    assert library.for_extension(".xyz") is None


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_library(tmp_path / "nope")


def test_duplicate_extension_raises(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "profile.yaml").write_text(
        "name: a\nfile_extensions: ['.x']\n"
    )
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "profile.yaml").write_text(
        "name: b\nfile_extensions: ['.x']\n"
    )
    with pytest.raises(ConfigurationError, match="extension"):
        load_library(tmp_path)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "profile.yaml").write_text(": : not valid")
    with pytest.raises(ConfigurationError):
        load_library(tmp_path)


def test_empty_library_returns_none_for_lookup() -> None:
    library = LanguageLibrary()
    assert library.for_file("x.py") is None
    assert library.names() == []
