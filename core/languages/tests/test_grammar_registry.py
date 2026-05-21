"""Grammar registry tests: native class loading + LLM fallback."""

from __future__ import annotations

from core.languages import load_library
from core.languages.grammar_registry import build_grammars
from core.languages.library import DEFAULT_LANGUAGES_DIR
from core.languages.java.grammar import JavaGrammar
from core.languages.python.tree_sitter_grammar import TreeSitterPythonGrammar
from ingestion.grammars import ConfigGrammar, LLMGrammar


def test_build_grammars_loads_native_drivers() -> None:
    grammars = build_grammars(load_library(DEFAULT_LANGUAGES_DIR))
    types = {type(g).__name__ for g in grammars}
    # Python's profile.yaml now wires the tree-sitter grammar as the default.
    assert "TreeSitterPythonGrammar" in types
    assert "JavaGrammar" in types
    # ConfigGrammar always added so YAML/TOML/properties facts feed resolvers.
    assert "ConfigGrammar" in types


def test_llm_grammar_is_the_unrecognised_language_fallback() -> None:
    """The registry adds an LLMGrammar that claims common code extensions
    (`.go`, `.rb`, `.ts`, …) the native grammars don't cover. This is the
    seam the LLM-extract pipeline rides on; without it, an unknown
    language is silently skipped by the walker.

    Note that the LLMGrammar MUST NOT claim any suffix already owned by
    a native grammar — those would never reach it (walker iterates in
    order), but it would clutter `--list-grammars` and is wrong on
    principle."""
    from pathlib import Path

    grammars = build_grammars(load_library(DEFAULT_LANGUAGES_DIR))
    llms = [g for g in grammars if isinstance(g, LLMGrammar)]
    assert len(llms) == 1, "expected exactly one LLMGrammar fallback"
    llm = llms[0]
    # Claims at least one common unrecognised-language extension.
    assert ".go" in llm.suffixes
    assert ".rb" in llm.suffixes
    assert ".ts" in llm.suffixes
    # Doesn't claim Python or Java — those are native.
    assert ".py" not in llm.suffixes
    assert ".java" not in llm.suffixes
    # And the matcher follows the claim list.
    assert llm.matches(Path("foo.go"))
    assert not llm.matches(Path("foo.py"))


def test_native_grammar_matches_its_extension() -> None:
    grammars = build_grammars(load_library(DEFAULT_LANGUAGES_DIR))
    from pathlib import Path

    py_g = next(g for g in grammars if isinstance(g, TreeSitterPythonGrammar))
    java_g = next(g for g in grammars if isinstance(g, JavaGrammar))
    cfg_g = next(g for g in grammars if isinstance(g, ConfigGrammar))

    assert py_g.matches(Path("x.py"))
    assert not py_g.matches(Path("Foo.java"))
    assert java_g.matches(Path("Foo.java"))
    assert cfg_g.matches(Path("application.yml"))


def test_walker_routes_plsql_through_native_grammar(tmp_path) -> None:
    """End-to-end: a `.pks` file routes to the native `PlSqlGrammar` and
    produces structural facts (package, procedure, ...) without crashing."""
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
    files = set(tree.files())
    assert any(f.endswith("main.py") for f in files)
    assert any(f.endswith("charges.pks") for f in files)
    # Native grammar emits a CLASS_DEF for the PACKAGE.
    from core.facts import FactKind
    class_defs = tree.where(kind=FactKind.CLASS_DEF)
    assert any(f.data.get("name") == "charges" for f in class_defs)
