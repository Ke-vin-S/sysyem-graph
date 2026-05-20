"""Tests for `resolve_db_access`."""

from __future__ import annotations

from core.facts import Fact, FactKind, FactTree
from core.resolvers.db_access_resolver import resolve_db_access
from core.types import CodeArtifact, LineRange


def _proc(repo: str, pkg: str, name: str, *, file: str, line: int = 1) -> CodeArtifact:
    aid = f"proc:{repo}:{pkg}:{name}" if pkg else f"proc:{repo}::{name}"
    return CodeArtifact(
        id=aid,
        repoId=repo,
        type="procedure",
        name=name,
        file=file,
        lineRange=LineRange(start=line, end=line + 10),
        isPublic=True,
        producedBy="test",
    )


def _symbol(file: str, line: int, *, name: str, pkg: str = "") -> Fact:
    return Fact(
        kind=FactKind.SYMBOL,
        file=file,
        line=line,
        repo_id="r",
        data={
            "sym_kind": "procedure",
            "name": name,
            "enclosing_package": pkg,
            "enclosing_class": pkg,
        },
    )


def _sql(file: str, line: int, *, op: str, tables: list[str]) -> Fact:
    return Fact(
        kind=FactKind.SQL_STATEMENT,
        file=file,
        line=line,
        repo_id="r",
        data={
            "operation": op,
            "tables": tables,
            "target_proc": "",
            "enclosing_symbol": "",
            "raw": "",
        },
    )


def test_select_populates_reads() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("p.pkb", 2, name="charge", pkg="pkg_p"),
        _sql("p.pkb", 5, op="select", tables=["invoice"]),
    ])
    out = resolve_db_access(
        trees={"r": tree},
        artifacts=[_proc("r", "pkg_p", "charge", file="p.pkb", line=2)],
    )
    charge = next(a for a in out.artifacts if a.name == "charge")
    assert "table:r:invoice" in charge.reads
    assert charge.writes == ()


def test_insert_update_delete_merge_populate_writes() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("p.pkb", 2, name="charge", pkg="pkg_p"),
        _sql("p.pkb", 5, op="insert", tables=["charge_log"]),
        _sql("p.pkb", 6, op="update", tables=["customer"]),
        _sql("p.pkb", 7, op="delete", tables=["tmp"]),
        _sql("p.pkb", 8, op="merge", tables=["audit"]),
    ])
    out = resolve_db_access(
        trees={"r": tree},
        artifacts=[_proc("r", "pkg_p", "charge", file="p.pkb", line=2)],
    )
    charge = next(a for a in out.artifacts if a.name == "charge")
    assert set(charge.writes) == {
        "table:r:charge_log",
        "table:r:customer",
        "table:r:tmp",
        "table:r:audit",
    }


def test_emits_one_table_artifact_per_unique_table() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("p.pkb", 2, name="a", pkg="p"),
        _symbol("p.pkb", 20, name="b", pkg="p"),
        _sql("p.pkb", 5, op="select", tables=["invoice"]),
        _sql("p.pkb", 22, op="update", tables=["invoice"]),
    ])
    out = resolve_db_access(
        trees={"r": tree},
        artifacts=[
            _proc("r", "p", "a", file="p.pkb", line=2),
            _proc("r", "p", "b", file="p.pkb", line=20),
        ],
    )
    assert len(out.table_artifacts) == 1
    assert out.table_artifacts[0].name == "invoice"
    assert out.table_artifacts[0].type == "table"


def test_call_and_execute_are_ignored() -> None:
    """`call`/`execute` are code-call ops handled by OracleCallResolver, not DB access."""
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("p.pkb", 2, name="x", pkg="p"),
        _sql("p.pkb", 5, op="call", tables=[]),
        _sql("p.pkb", 6, op="execute", tables=[]),
    ])
    out = resolve_db_access(
        trees={"r": tree},
        artifacts=[_proc("r", "p", "x", file="p.pkb", line=2)],
    )
    assert out.table_artifacts == []
    x = next(a for a in out.artifacts if a.name == "x")
    assert x.reads == ()
    assert x.writes == ()


def test_truncate_is_a_write() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("p.pkb", 2, name="x", pkg="p"),
        _sql("p.pkb", 5, op="truncate", tables=["staging"]),
    ])
    out = resolve_db_access(
        trees={"r": tree},
        artifacts=[_proc("r", "p", "x", file="p.pkb", line=2)],
    )
    x = next(a for a in out.artifacts if a.name == "x")
    assert "table:r:staging" in x.writes


def test_schema_qualified_table_preserved() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("p.pkb", 2, name="x", pkg="p"),
        _sql("p.pkb", 5, op="select", tables=["app.customer"]),
    ])
    out = resolve_db_access(
        trees={"r": tree},
        artifacts=[_proc("r", "p", "x", file="p.pkb", line=2)],
    )
    assert any(t.name == "app.customer" for t in out.table_artifacts)
