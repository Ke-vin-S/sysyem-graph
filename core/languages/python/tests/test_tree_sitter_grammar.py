"""TreeSitterPythonGrammar tests.

Two layers:
  1. Parity — every assertion in `test_grammar.py` (the legacy AST-based
     grammar) must also hold for the tree-sitter grammar. We import the
     same SAMPLE source and re-assert.
  2. New fields — params_rich/self_assignments/receiver_chain/ASSIGNMENT
     emissions the legacy grammar didn't produce.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.facts import FactKind
from core.languages.python.tree_sitter_grammar import TreeSitterPythonGrammar


@pytest.fixture(scope="module")
def grammar() -> TreeSitterPythonGrammar:
    return TreeSitterPythonGrammar()


SAMPLE = '''\
from fastapi import FastAPI, APIRouter
import httpx

app = FastAPI(root_path="/v1")
router = APIRouter()


@router.get("/charges/{id}")
def get_charge(id: int):
    return httpx.get("http://x")


class Helper:
    @staticmethod
    def compute(x):
        return x * 2


app.include_router(router, prefix="/payments")
'''


# ---- parity with the legacy grammar's seven assertions --------------------


def test_imports_emitted(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    imports = [f for f in facts if f.kind is FactKind.IMPORT]
    modules = {f.data["module"] for f in imports}
    assert modules == {"fastapi", "httpx"}


def test_symbols_emitted_with_enclosing_class(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    symbols = [f for f in facts if f.kind is FactKind.SYMBOL]
    by_name = {f.data["name"]: f for f in symbols}
    assert "get_charge" in by_name
    assert by_name["get_charge"].data["enclosing_class"] == ""
    assert by_name["compute"].data["enclosing_class"] == "Helper"


def test_class_def_emitted(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    classes = [f for f in facts if f.kind is FactKind.CLASS_DEF]
    assert [c.data["name"] for c in classes] == ["Helper"]


def test_decorator_captures_callee_and_args(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    decorators = [f for f in facts if f.kind is FactKind.DECORATOR]
    route_dec = next(d for d in decorators if d.data["callee"] == "router.get")
    assert route_dec.data["args"] == ["/charges/{id}"]
    assert route_dec.data["target_symbol"] == "get_charge"


def test_call_fastapi_constructor(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    calls = [f for f in facts if f.kind is FactKind.CALL and f.data["callee"] == "FastAPI"]
    assert len(calls) == 1
    assert calls[0].data["kwargs"] == {"root_path": "/v1"}


def test_call_include_router_kwargs(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    calls = [f for f in facts if f.kind is FactKind.CALL and f.data["method"] == "include_router"]
    assert len(calls) == 1
    assert calls[0].data["kwargs"] == {"prefix": "/payments"}
    assert calls[0].data["args"] == ["<name:router>"]


def test_syntax_error_returns_facts_we_can_recover(grammar: TreeSitterPythonGrammar) -> None:
    """Tree-sitter recovers on partial input — unlike `ast.parse` which
    returns []. The contract is "never raise"; we don't insist on emptiness."""
    out = grammar.extract(Path("broken.py"), "def : :", repo_id="r")
    assert isinstance(out, list)


# ---- new fields the legacy grammar can't carry ---------------------------


def test_params_rich_and_legacy_both_emitted(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(
        Path("svc.py"),
        "def handle(id: int, body: dict, *, debug: bool = False, **kw): pass\n",
        repo_id="r",
    )
    sym = next(f for f in facts if f.kind is FactKind.SYMBOL)
    # Legacy shape stays — resolvers built on it keep working.
    assert sym.data["params"] == [
        ("id", "int"), ("body", "dict"), ("debug", "bool"), ("kw", ""),
    ]
    # Rich shape carries kind discrimination.
    rich = {p["name"]: p for p in sym.data["params_rich"]}
    assert rich["id"]["kind"] == "pos"
    assert rich["debug"]["kind"] == "kw"
    assert rich["debug"]["default_kind"] == "default"
    assert rich["kw"]["kind"] == "kwvar"


def test_self_assignments_captured_in_init(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(
        Path("svc.py"),
        "class S:\n"
        "    def __init__(self, repo: R, db):\n"
        "        self.repo = repo\n"
        "        self.db = make_db()\n"
        "        self.count: int = 0\n",
        repo_id="r",
    )
    init = next(
        f for f in facts
        if f.kind is FactKind.SYMBOL and f.data["name"] == "__init__"
    )
    by_attr = {sa["attr"]: sa for sa in init.data["self_assignments"]}
    assert by_attr["repo"]["source_kind"] == "name"
    assert by_attr["repo"]["source"] == "repo"
    assert by_attr["db"]["source_kind"] == "call"
    assert by_attr["db"]["source"] == "make_db"
    assert by_attr["count"]["source_kind"] == "literal"
    assert by_attr["count"]["type_hint"] == "int"


def test_class_def_carries_init_params(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(
        Path("svc.py"),
        "class S:\n"
        "    def __init__(self, repo: UserRepository, db: Session):\n"
        "        pass\n",
        repo_id="r",
    )
    cls = next(f for f in facts if f.kind is FactKind.CLASS_DEF)
    assert cls.data["init_params"] == [
        ("repo", "UserRepository"), ("db", "Session"),
    ]


def test_module_assignment_emitted(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(
        Path("app.py"),
        "from app.db import Database\n"
        "db = Database()\n",
        repo_id="r",
    )
    assigns = [f for f in facts if f.kind is FactKind.ASSIGNMENT]
    assert len(assigns) == 1
    a = assigns[0]
    assert a.data["target"] == "db"
    assert a.data["target_chain"] == ["db"]
    assert a.data["source_kind"] == "call"
    assert a.data["source"] == "Database"
    assert a.data["scope"] == "module"


def test_self_attr_assignment_emitted_separately(grammar: TreeSitterPythonGrammar) -> None:
    """Beyond the denormalized list on the method SYMBOL, every `self.X = …`
    inside a method body is also a standalone ASSIGNMENT fact at
    scope=method. That keeps the resolver flexible — it can index over
    ASSIGNMENT facts directly without re-walking SYMBOLs."""
    facts = grammar.extract(
        Path("svc.py"),
        "class S:\n"
        "    def __init__(self, x):\n"
        "        self.x = x\n",
        repo_id="r",
    )
    assigns = [f for f in facts if f.kind is FactKind.ASSIGNMENT]
    assert len(assigns) == 1
    a = assigns[0]
    assert a.data["target_chain"] == ["self", "x"]
    assert a.data["scope"] == "method"


def test_call_receiver_chain_decomposes_self_attr(grammar: TreeSitterPythonGrammar) -> None:
    """`self.repo.get(id)` exposes receiver_chain=['self','repo'] for the
    resolver — that's the data path that lets `self.attr.method()` resolve
    without any grammar edits later."""
    facts = grammar.extract(
        Path("svc.py"),
        "class S:\n"
        "    def f(self):\n"
        "        return self.repo.get(1)\n",
        repo_id="r",
    )
    calls = [f for f in facts if f.kind is FactKind.CALL]
    target = next(c for c in calls if c.data["method"] == "get")
    assert target.data["receiver"] == "self.repo"
    assert target.data["receiver_chain"] == ["self", "repo"]


def test_facts_emitted_inside_try_except_finally(grammar: TreeSitterPythonGrammar) -> None:
    """Imports, assignments, and nested defs inside `try` / `except` /
    `finally` / `elif` / `else` blocks must all surface as facts. The
    previous walker only descended into compound statements but stopped
    at their sub-clauses — every branch except `try`'s body was dropping
    structural facts silently."""
    src = (
        "try:\n"
        "    from a import x\n"
        "    a_val = x()\n"
        "except ImportError:\n"
        "    from b import x\n"
        "    a_val = None\n"
        "finally:\n"
        "    from c import done\n"
        "if FLAG:\n"
        "    from p import handler\n"
        "else:\n"
        "    from s import handler\n"
    )
    facts = grammar.extract(Path("x.py"), src, repo_id="r")
    modules = {f.data["module"] for f in facts if f.kind is FactKind.IMPORT}
    assert modules == {"a", "b", "c", "p", "s"}
    # Both the try-body and except-block assignments emit at module scope.
    assigns = [f for f in facts if f.kind is FactKind.ASSIGNMENT]
    assert len(assigns) == 2
    assert {a.data["source_kind"] for a in assigns} == {"call", "literal"}


def test_relative_import_level_captured(grammar: TreeSitterPythonGrammar) -> None:
    facts = grammar.extract(
        Path("pkg/sub/x.py"),
        "from ..util import helper\n",
        repo_id="r",
    )
    imp = next(f for f in facts if f.kind is FactKind.IMPORT)
    assert imp.data["module"] == "util"
    assert imp.data["level"] == 2
    assert imp.data["names"] == ["helper"]
