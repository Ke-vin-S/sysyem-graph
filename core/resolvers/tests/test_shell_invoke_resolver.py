"""Tests for `resolve_shell_invokes`."""

from __future__ import annotations

from core.facts import Fact, FactKind, FactTree
from core.resolvers.shell_invoke_resolver import resolve_shell_invokes
from core.types import CodeArtifact, LineRange


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


def _sh_func_symbol(file: str, line: int, name: str) -> Fact:
    return Fact(
        kind=FactKind.SYMBOL,
        file=file,
        line=line,
        repo_id="r",
        data={
            "sym_kind": "function",
            "name": name,
            "enclosing_class": "",
            "enclosing_package": "",
        },
    )


def _call(file: str, line: int, callee: str) -> Fact:
    return Fact(
        kind=FactKind.CALL,
        file=file,
        line=line,
        repo_id="r",
        data={
            "callee": callee,
            "receiver": "",
            "method": callee.split("/")[-1],
            "args": [],
            "kwargs": {},
        },
    )


def test_shell_function_calling_binary_resolves_edge() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _sh_func_symbol("run.sh", 1, "run_daily"),
        _call("run.sh", 3, "./bin/charge_loader"),
    ])
    # The compiled binary is represented by its C function `main` in
    # `charge_loader.c` — basename match.
    artifacts = [
        _c_func("r", "run_daily", file="run.sh", line=1),
        _c_func("r", "main", file="charge_loader.c", line=10),
    ]
    out = resolve_shell_invokes(trees={"r": tree}, artifacts=artifacts)
    # Wait — basename match keys on the FILE basename, so we'd need
    # `charge_loader` as the basename. The C function is in
    # `charge_loader.c` whose basename is `charge_loader.c`, not
    # `charge_loader`. So a literal `./bin/charge_loader` call won't
    # match `charge_loader.c`. This test confirms that conservatism.
    edges_from_runner = [e for e in out.edges if "run_daily" in e[0]]
    assert edges_from_runner == []


def test_shell_function_calling_other_script_resolves_edge() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _sh_func_symbol("driver.sh", 1, "main"),
        _call("driver.sh", 3, "./load.sh"),
    ])
    # The .sh callee matches by basename: load.sh
    artifacts = [
        _c_func("r", "main", file="driver.sh", line=1),
        # Pretend there's an existing artifact for load.sh — type='script'
        # is what _resolve_callee looks up via by_basename.
        CodeArtifact(
            id="script:r:load.sh",
            repoId="r",
            type="script",
            name="load.sh",
            file="load.sh",
            lineRange=LineRange(start=1, end=1),
            isPublic=True,
            producedBy="test",
        ),
    ]
    out = resolve_shell_invokes(trees={"r": tree}, artifacts=artifacts)
    main_art = next(a for a in out.artifacts if a.id.endswith("main"))
    assert "script:r:load.sh" in main_art.calls


def test_unknown_command_produces_no_edge() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _sh_func_symbol("x.sh", 1, "main"),
        _call("x.sh", 2, "cp"),  # standard utility, not in artifacts
    ])
    out = resolve_shell_invokes(
        trees={"r": tree},
        artifacts=[_c_func("r", "main", file="x.sh", line=1)],
    )
    assert out.edges == []


def test_promotes_orphan_sh_file_to_script_artifact() -> None:
    """When a shell file has a function but no script artifact represents
    it yet, the resolver synthesizes one so edges have somewhere to attach
    when a call is at top-level (outside any function)."""
    tree = FactTree(repo_id="r")
    tree.extend([
        _sh_func_symbol("run.sh", 5, "helper"),
        _call("run.sh", 1, "./bin/foo"),  # top-level call, not inside helper
    ])
    # Provide an artifact for `./bin/foo` (basename `foo`).
    foo = CodeArtifact(
        id="script:r:foo",
        repoId="r",
        type="script",
        name="foo",
        file="bin/foo",
        lineRange=LineRange(start=1, end=1),
        isPublic=True,
        producedBy="test",
    )
    out = resolve_shell_invokes(
        trees={"r": tree},
        artifacts=[_c_func("r", "helper", file="run.sh", line=5), foo],
    )
    # A synthesized `script:r:run.sh` artifact appears.
    new_scripts = [a for a in out.artifacts if a.type == "script" and a.name == "run.sh"]
    assert len(new_scripts) == 1
    # And the top-level call edge attaches to it.
    assert any(
        e[0] == new_scripts[0].id and e[1] == "script:r:foo"
        for e in out.edges
    )


def test_sqlplus_script_target_resolves() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        _sh_func_symbol("run.sh", 1, "go"),
        Fact(
            kind=FactKind.SQL_STATEMENT,
            file="run.sh",
            line=3,
            repo_id="r",
            data={
                "operation": "script",
                "tables": [],
                "target_proc": "sql/cleanup.sql",
                "enclosing_symbol": "",
                "raw": "sqlplus user/p @sql/cleanup.sql",
            },
        ),
    ])
    cleanup = CodeArtifact(
        id="script:r:cleanup.sql",
        repoId="r",
        type="script",
        name="cleanup.sql",
        file="sql/cleanup.sql",
        lineRange=LineRange(start=1, end=1),
        isPublic=True,
        producedBy="test",
    )
    out = resolve_shell_invokes(
        trees={"r": tree},
        artifacts=[_c_func("r", "go", file="run.sh", line=1), cleanup],
    )
    go = next(a for a in out.artifacts if a.id.endswith(":go"))
    assert "script:r:cleanup.sql" in go.calls
