"""FunctionCallResolver tests: CALL fact → (caller, callee) artifact edges."""

from __future__ import annotations

from core.facts import Fact, FactKind, FactTree
from core.resolvers import FunctionCallResolver
from core.types import CodeArtifact, LineRange


def _fn(name: str, *, file: str, start: int, end: int, repo: str = "r") -> CodeArtifact:
    return CodeArtifact(
        id=f"fn:{repo}:{file}:{name}",
        repoId=repo,
        type="function",
        name=name,
        file=file,
        lineRange=LineRange(start=start, end=end),
        isPublic=True,
    )


def _import_from(file: str, module: str, names: list[str], level: int = 0) -> Fact:
    return Fact(
        kind=FactKind.IMPORT,
        file=file,
        line=1,
        repo_id="r",
        data={"module": module, "names": names, "alias": "", "level": level},
    )


def _import_bare(file: str, module: str, alias: str = "") -> Fact:
    return Fact(
        kind=FactKind.IMPORT,
        file=file,
        line=1,
        repo_id="r",
        data={"module": module, "names": [], "alias": alias},
    )


def _call(file: str, line: int, *, callee: str) -> Fact:
    receiver, _, method = callee.rpartition(".")
    return Fact(
        kind=FactKind.CALL,
        file=file,
        line=line,
        repo_id="r",
        data={
            "callee": callee,
            "receiver": receiver,
            "method": method,
            "args": [],
            "kwargs": {},
        },
    )


def test_same_file_call_links_caller_to_callee() -> None:
    file = "src/util.py"
    caller = _fn("foo", file=file, start=1, end=5)
    callee = _fn("bar", file=file, start=10, end=12)
    tree = FactTree.from_facts("r", [_call(file, line=3, callee="bar")])
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee])
    updated = {a.name: a for a in out.artifacts}
    assert updated["foo"].calls == (callee.id,)
    assert updated["bar"].calls == ()
    assert len(out.edges) == 1


def test_from_import_named_call_resolves() -> None:
    caller_file = "src/app.py"
    target_file = "src/util.py"
    caller = _fn("run", file=caller_file, start=5, end=10)
    callee = _fn("helper", file=target_file, start=1, end=3)
    tree = FactTree.from_facts(
        "r",
        [
            _import_from(caller_file, "src.util", ["helper"]),
            _call(caller_file, line=7, callee="helper"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee])
    updated = {a.name: a for a in out.artifacts}
    assert updated["run"].calls == (callee.id,)


def test_import_module_attribute_call_resolves() -> None:
    caller_file = "src/app.py"
    target_file = "src/util.py"
    caller = _fn("run", file=caller_file, start=5, end=10)
    callee = _fn("helper", file=target_file, start=1, end=3)
    tree = FactTree.from_facts(
        "r",
        [
            _import_bare(caller_file, "src.util"),
            _call(caller_file, line=7, callee="src.util.helper"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee])
    updated = {a.name: a for a in out.artifacts}
    assert updated["run"].calls == (callee.id,)


def test_import_as_alias_resolves_via_alias() -> None:
    caller_file = "src/app.py"
    target_file = "src/util.py"
    caller = _fn("run", file=caller_file, start=5, end=10)
    callee = _fn("helper", file=target_file, start=1, end=3)
    tree = FactTree.from_facts(
        "r",
        [
            _import_bare(caller_file, "src.util", alias="u"),
            _call(caller_file, line=7, callee="u.helper"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee])
    updated = {a.name: a for a in out.artifacts}
    assert updated["run"].calls == (callee.id,)


def test_reexport_through_init_resolves() -> None:
    """from pkg import helper, where pkg/__init__.py does `from pkg.util import helper`."""
    caller_file = "src/app.py"
    init_file = "pkg/__init__.py"
    target_file = "pkg/util.py"
    caller = _fn("run", file=caller_file, start=5, end=10)
    callee = _fn("helper", file=target_file, start=1, end=3)
    tree = FactTree.from_facts(
        "r",
        [
            _import_from(init_file, "pkg.util", ["helper"]),
            _import_from(caller_file, "pkg", ["helper"]),
            _call(caller_file, line=7, callee="helper"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee])
    updated = {a.name: a for a in out.artifacts}
    assert updated["run"].calls == (callee.id,)


def test_unresolvable_third_party_call_emits_no_edge() -> None:
    """A call to httpx.get with no local artifact named `get` in `httpx`."""
    caller_file = "src/app.py"
    caller = _fn("run", file=caller_file, start=5, end=10)
    tree = FactTree.from_facts(
        "r",
        [
            _import_bare(caller_file, "httpx"),
            _call(caller_file, line=7, callee="httpx.get"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller])
    assert out.artifacts[0].calls == ()
    assert out.edges == []


def test_module_level_call_has_no_caller() -> None:
    """A call outside any function should produce no edge."""
    file = "src/app.py"
    callee = _fn("helper", file=file, start=10, end=12)
    tree = FactTree.from_facts(
        "r",
        # call at line 1, before any function definition's body range
        [_call(file, line=1, callee="helper")],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[callee])
    assert out.edges == []
    assert out.artifacts[0].calls == ()


def test_recursive_self_call_is_skipped() -> None:
    file = "src/x.py"
    fn = _fn("recurse", file=file, start=1, end=5)
    tree = FactTree.from_facts("r", [_call(file, line=3, callee="recurse")])
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[fn])
    assert out.artifacts[0].calls == ()


def test_duplicate_call_pairs_deduped() -> None:
    file = "src/x.py"
    caller = _fn("foo", file=file, start=1, end=10)
    callee = _fn("bar", file=file, start=15, end=17)
    tree = FactTree.from_facts(
        "r",
        [_call(file, line=3, callee="bar"), _call(file, line=5, callee="bar")],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee])
    assert out.artifacts[0].calls == (callee.id,)
    assert len(out.edges) == 1
