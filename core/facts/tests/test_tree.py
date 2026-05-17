"""Tests for the FactTree indices and query operators."""

from __future__ import annotations

import pytest

from core.facts import Fact, FactKind, FactTree


def _fact(kind: FactKind, *, file: str, line: int, line_end: int | None = None, **data) -> Fact:
    return Fact(kind=kind, file=file, line=line, line_end=line_end, repo_id="r", data=data)


def test_id_is_stable_and_unique() -> None:
    a = _fact(FactKind.SYMBOL, file="x.py", line=1, sym_kind="function", name="foo")
    b = _fact(FactKind.SYMBOL, file="x.py", line=1, sym_kind="function", name="foo")
    c = _fact(FactKind.SYMBOL, file="x.py", line=1, sym_kind="function", name="bar")
    assert a.id == b.id
    assert a.id != c.id


def test_where_by_kind() -> None:
    tree = FactTree.from_facts(
        "r",
        [
            _fact(FactKind.SYMBOL, file="a.py", line=1),
            _fact(FactKind.SYMBOL, file="b.py", line=1),
            _fact(FactKind.IMPORT, file="a.py", line=2),
        ],
    )
    syms = tree.where(kind=FactKind.SYMBOL)
    assert {f.file for f in syms} == {"a.py", "b.py"}
    assert tree.where(kind=FactKind.IMPORT, file="a.py")[0].kind is FactKind.IMPORT


def test_where_by_file() -> None:
    tree = FactTree.from_facts(
        "r",
        [
            _fact(FactKind.SYMBOL, file="a.py", line=1),
            _fact(FactKind.IMPORT, file="a.py", line=2),
            _fact(FactKind.SYMBOL, file="b.py", line=1),
        ],
    )
    assert len(tree.by_file("a.py")) == 2
    assert tree.by_file("missing.py") == []


def test_symbol_at_returns_nearest_following() -> None:
    tree = FactTree.from_facts(
        "r",
        [
            _fact(FactKind.SYMBOL, file="x.py", line=20, sym_kind="function", name="later"),
            _fact(FactKind.SYMBOL, file="x.py", line=12, sym_kind="function", name="nearest"),
            _fact(FactKind.SYMBOL, file="x.py", line=5, sym_kind="function", name="before"),
        ],
    )
    found = tree.symbol_at(file="x.py", line_after=10)
    assert found is not None
    assert found.data["name"] == "nearest"


def test_symbol_at_returns_none_when_no_following_symbol() -> None:
    tree = FactTree.from_facts(
        "r",
        [_fact(FactKind.SYMBOL, file="x.py", line=5, sym_kind="function", name="only")],
    )
    assert tree.symbol_at(file="x.py", line_after=10) is None


def test_enclosing_class_by_range() -> None:
    tree = FactTree.from_facts(
        "r",
        [
            _fact(FactKind.CLASS_DEF, file="x.py", line=5, line_end=30, name="Outer"),
            _fact(FactKind.CLASS_DEF, file="x.py", line=10, line_end=20, name="Inner"),
            _fact(FactKind.SYMBOL, file="x.py", line=15, sym_kind="method", name="m"),
        ],
    )
    target = tree.where(kind=FactKind.SYMBOL)[0]
    enclosing = tree.enclosing_class(target)
    assert enclosing is not None
    assert enclosing.data["name"] == "Inner"


def test_enclosing_class_returns_none_when_outside() -> None:
    tree = FactTree.from_facts(
        "r",
        [
            _fact(FactKind.CLASS_DEF, file="x.py", line=5, line_end=10, name="C"),
            _fact(FactKind.SYMBOL, file="x.py", line=20, sym_kind="function", name="loose"),
        ],
    )
    target = tree.where(kind=FactKind.SYMBOL)[0]
    assert tree.enclosing_class(target) is None


def test_files_lists_sorted() -> None:
    tree = FactTree.from_facts(
        "r",
        [
            _fact(FactKind.SYMBOL, file="b.py", line=1),
            _fact(FactKind.SYMBOL, file="a.py", line=1),
        ],
    )
    assert tree.files() == ["a.py", "b.py"]


def test_extra_field_rejected_on_fact() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Fact(kind=FactKind.SYMBOL, file="x.py", line=1, repo_id="r", bogus=1)  # type: ignore[call-arg]


def test_fact_frozen() -> None:
    from pydantic import ValidationError

    fact = _fact(FactKind.SYMBOL, file="x.py", line=1)
    with pytest.raises(ValidationError):
        fact.line = 99  # type: ignore[misc]
