"""Grammar registry tests: native class loading + LLM fallback."""

from __future__ import annotations

from core.languages import load_library
from core.languages.grammar_registry import build_grammars
from core.languages.library import DEFAULT_LANGUAGES_DIR
from core.languages.java.grammar import JavaGrammar
from core.languages.python.grammar import PythonGrammar
from ingestion.grammars import ConfigGrammar, LLMGrammar


def test_build_grammars_loads_native_drivers() -> None:
    grammars = build_grammars(load_library(DEFAULT_LANGUAGES_DIR))
    types = {type(g).__name__ for g in grammars}
    assert "PythonGrammar" in types
    assert "JavaGrammar" in types
    # ConfigGrammar always added so YAML/TOML/properties facts feed resolvers.
    assert "ConfigGrammar" in types


def test_llm_grammar_claims_plsql_extensions() -> None:
    grammars = build_grammars(load_library(DEFAULT_LANGUAGES_DIR))
    llms = [g for g in grammars if isinstance(g, LLMGrammar)]
    assert len(llms) == 1
    # PL/SQL contributes .sql/.pks/.pkb to the LLMGrammar's claimed suffixes.
    assert set(llms[0].suffixes) >= {".sql", ".pks", ".pkb"}


def test_native_grammar_matches_its_extension() -> None:
    grammars = build_grammars(load_library(DEFAULT_LANGUAGES_DIR))
    from pathlib import Path

    py_g = next(g for g in grammars if isinstance(g, PythonGrammar))
    java_g = next(g for g in grammars if isinstance(g, JavaGrammar))
    cfg_g = next(g for g in grammars if isinstance(g, ConfigGrammar))

    assert py_g.matches(Path("x.py"))
    assert not py_g.matches(Path("Foo.java"))
    assert java_g.matches(Path("Foo.java"))
    assert cfg_g.matches(Path("application.yml"))


def test_walker_routes_plsql_through_llm_grammar(tmp_path) -> None:
    """End-to-end: a .pks file routes to LLMGrammar (NullClient → 0 facts)
    without crashing the walk."""
    from core.languages.grammar_registry import build_grammars
    from core.walker import Walker

    (tmp_path / "payments").mkdir()
    (tmp_path / "payments" / "charges.pks").write_text(
        "CREATE OR REPLACE PACKAGE charges AS\n"
        "  PROCEDURE create_charge(amount NUMBER);\n"
        "END charges;\n"
    )
    (tmp_path / "main.py").write_text("def f(): pass\n")

    grammars = build_grammars(load_library(DEFAULT_LANGUAGES_DIR))
    walker = Walker(grammars=grammars)
    tree = walker.walk(tmp_path, repo_id="r")
    # The Python file produces facts; the .pks file produces 0 (NullClient).
    files = set(tree.files())
    assert any(f.endswith("main.py") for f in files)
    # The walk completed without raising.
