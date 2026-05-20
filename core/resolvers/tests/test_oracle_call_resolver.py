"""Tests for `resolve_oracle_calls`."""

from __future__ import annotations

from core.facts import Fact, FactKind, FactTree
from core.resolvers.oracle_call_resolver import resolve_oracle_calls
from core.types import CodeArtifact, LineRange


def _proc(repo: str, pkg: str, name: str, *, file: str, line: int = 1) -> CodeArtifact:
    aid = f"proc:{repo}:{pkg}:{name}" if pkg else f"proc:{repo}::{name}"
    return CodeArtifact(
        id=aid,
        repoId=repo,
        type="procedure",
        name=name,
        file=file,
        lineRange=LineRange(start=line, end=line + 5),
        isPublic=True,
        producedBy="test",
    )


def _c_func(repo: str, name: str, *, file: str, line: int = 1) -> CodeArtifact:
    return CodeArtifact(
        id=f"fn:{repo}:{file}:{name}",
        repoId=repo,
        type="function",
        name=name,
        file=file,
        lineRange=LineRange(start=line, end=line + 5),
        isPublic=True,
        producedBy="test",
    )


def _symbol(file: str, line: int, *, name: str, sym_kind: str = "procedure", pkg: str = "") -> Fact:
    return Fact(
        kind=FactKind.SYMBOL,
        file=file,
        line=line,
        repo_id="r",
        data={
            "sym_kind": sym_kind,
            "name": name,
            "enclosing_package": pkg,
            "enclosing_class": pkg,
        },
    )


def _call(file: str, line: int, *, callee: str) -> Fact:
    receiver, _, method = callee.partition(".")
    return Fact(
        kind=FactKind.CALL,
        file=file,
        line=line,
        repo_id="r",
        data={
            "callee": callee,
            "receiver": receiver if method else "",
            "method": method or callee,
            "args": [],
            "kwargs": {},
        },
    )


def test_resolves_qualified_cross_package_call() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("billing.pkb", 2, name="charge", pkg="pkg_billing"),
        _call("billing.pkb", 5, callee="pkg_audit.log"),
    ])
    artifacts = [
        _proc("r", "pkg_billing", "charge", file="billing.pkb", line=2),
        _proc("r", "pkg_audit", "log", file="audit.pkb"),
    ]
    out = resolve_oracle_calls(trees={"r": tree}, artifacts=artifacts)
    charge = next(a for a in out.artifacts if a.name == "charge")
    assert "proc:r:pkg_audit:log" in charge.calls


def test_unqualified_call_resolves_in_same_repo() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("billing.pkb", 2, name="charge", pkg="pkg_billing"),
        _call("billing.pkb", 5, callee="helper"),
    ])
    artifacts = [
        _proc("r", "pkg_billing", "charge", file="billing.pkb", line=2),
        _proc("r", "pkg_billing", "helper", file="billing.pkb", line=20),
    ]
    out = resolve_oracle_calls(trees={"r": tree}, artifacts=artifacts)
    charge = next(a for a in out.artifacts if a.name == "charge")
    # Unqualified calls only resolve when there's a unique local match by name.
    assert "proc:r:pkg_billing:helper" in charge.calls


def test_unresolved_qualified_call_materializes_external_stub() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("p.pkb", 2, name="charge", pkg="pkg_p"),
        Fact(
            kind=FactKind.SQL_STATEMENT,
            file="p.pkb",
            line=4,
            repo_id="r",
            data={
                "operation": "call",
                "tables": [],
                "target_proc": "external_pkg.do_it",
                "enclosing_symbol": "",
                "raw": "",
            },
        ),
    ])
    artifacts = [_proc("r", "pkg_p", "charge", file="p.pkb", line=2)]
    out = resolve_oracle_calls(trees={"r": tree}, artifacts=artifacts)
    # A new external stub was materialized.
    stubs = [a for a in out.artifacts if a.repo_id == "external"]
    assert len(stubs) == 1
    assert stubs[0].name == "external_pkg.do_it"
    # And the calling procedure now references it.
    charge = next(a for a in out.artifacts if a.name == "charge")
    assert stubs[0].id in charge.calls


def test_pro_c_function_to_plsql_procedure_via_exec_sql() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("charge.pc", 3, name="run_billing", sym_kind="function"),
        Fact(
            kind=FactKind.SQL_STATEMENT,
            file="charge.pc",
            line=8,
            repo_id="r",
            data={
                "operation": "execute",
                "tables": [],
                "target_proc": "pkg_billing.charge",
                "enclosing_symbol": "",
                "raw": "BEGIN pkg_billing.charge(:id)",
            },
        ),
    ])
    artifacts = [
        _c_func("r", "run_billing", file="charge.pc", line=3),
        _proc("r", "pkg_billing", "charge", file="billing.pkb"),
    ]
    out = resolve_oracle_calls(trees={"r": tree}, artifacts=artifacts)
    run = next(a for a in out.artifacts if a.name == "run_billing")
    assert "proc:r:pkg_billing:charge" in run.calls


def test_no_edges_when_target_unknown_and_no_qualifier() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("p.pkb", 2, name="charge", pkg="pkg_p"),
        _call("p.pkb", 5, callee="totally_unknown"),
    ])
    out = resolve_oracle_calls(
        trees={"r": tree},
        artifacts=[_proc("r", "pkg_p", "charge", file="p.pkb", line=2)],
    )
    assert out.edges == []


def test_idempotent_on_re_run() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _symbol("p.pkb", 2, name="charge", pkg="pkg_p"),
        _call("p.pkb", 5, callee="pkg_x.go"),
    ])
    artifacts = [
        _proc("r", "pkg_p", "charge", file="p.pkb", line=2),
        _proc("r", "pkg_x", "go", file="x.pkb"),
    ]
    first = resolve_oracle_calls(trees={"r": tree}, artifacts=artifacts)
    second = resolve_oracle_calls(trees={"r": tree}, artifacts=first.artifacts)
    a1 = next(a for a in first.artifacts if a.name == "charge")
    a2 = next(a for a in second.artifacts if a.name == "charge")
    assert a1.calls == a2.calls  # No duplicate edges
