"""PythonGrammar -> Fact tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.facts import FactKind
from ingestion.grammars import PythonGrammar


@pytest.fixture
def grammar() -> PythonGrammar:
    return PythonGrammar()


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


def test_imports_emitted(grammar: PythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    imports = [f for f in facts if f.kind is FactKind.IMPORT]
    modules = {f.data["module"] for f in imports}
    assert modules == {"fastapi", "httpx"}


def test_symbols_emitted_with_enclosing_class(grammar: PythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    symbols = [f for f in facts if f.kind is FactKind.SYMBOL]
    by_name = {f.data["name"]: f for f in symbols}
    assert "get_charge" in by_name
    assert by_name["get_charge"].data["enclosing_class"] == ""
    assert by_name["compute"].data["enclosing_class"] == "Helper"


def test_class_def_emitted(grammar: PythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    classes = [f for f in facts if f.kind is FactKind.CLASS_DEF]
    assert [c.data["name"] for c in classes] == ["Helper"]


def test_decorator_captures_callee_and_args(grammar: PythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    decorators = [f for f in facts if f.kind is FactKind.DECORATOR]
    route_dec = next(d for d in decorators if d.data["callee"] == "router.get")
    assert route_dec.data["args"] == ["/charges/{id}"]
    assert route_dec.data["target_symbol"] == "get_charge"


def test_call_fastapi_constructor(grammar: PythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    calls = [f for f in facts if f.kind is FactKind.CALL and f.data["callee"] == "FastAPI"]
    assert len(calls) == 1
    assert calls[0].data["kwargs"] == {"root_path": "/v1"}


def test_call_include_router_kwargs(grammar: PythonGrammar) -> None:
    facts = grammar.extract(Path("app.py"), SAMPLE, repo_id="r")
    calls = [f for f in facts if f.kind is FactKind.CALL and f.data["method"] == "include_router"]
    assert len(calls) == 1
    assert calls[0].data["kwargs"] == {"prefix": "/payments"}
    assert calls[0].data["args"] == ["<name:router>"]


def test_syntax_error_returns_empty(grammar: PythonGrammar) -> None:
    assert grammar.extract(Path("broken.py"), "def : :", repo_id="r") == []
