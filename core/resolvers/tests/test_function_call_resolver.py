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


# ---- helpers for the typed-receiver tests below ---------------------------


def _method(
    name: str,
    *,
    cls: str,
    file: str,
    start: int,
    end: int,
    repo: str = "r",
) -> CodeArtifact:
    return CodeArtifact(
        id=f"method:{repo}:{file}:{cls}.{name}",
        repoId=repo,
        type="method",
        name=name,
        file=file,
        lineRange=LineRange(start=start, end=end),
        isPublic=True,
    )


def _symbol_method(
    *,
    name: str,
    cls: str,
    file: str,
    line: int,
    params: list[tuple[str, str]] | None = None,
    repo: str = "r",
) -> Fact:
    return Fact(
        kind=FactKind.SYMBOL,
        file=file,
        line=line,
        line_end=line + 1,
        repo_id=repo,
        data={
            "sym_kind": "method",
            "name": name,
            "is_async": False,
            "enclosing_class": cls,
            "references": [],
            "params": list(params or []),
        },
    )


def _symbol_fn(
    *,
    name: str,
    file: str,
    line: int,
    params: list[tuple[str, str]] | None = None,
    repo: str = "r",
) -> Fact:
    return Fact(
        kind=FactKind.SYMBOL,
        file=file,
        line=line,
        line_end=line + 1,
        repo_id=repo,
        data={
            "sym_kind": "function",
            "name": name,
            "is_async": False,
            "enclosing_class": "",
            "references": [],
            "params": list(params or []),
        },
    )


def test_self_dotted_call_resolves_to_sibling_method() -> None:
    """A method calling `self.helper()` should link to the sibling method
    on the same class — the data is in `enclosing_class`, the resolver just
    needs to consult it."""
    file = "src/svc.py"
    caller = _method("do", cls="Svc", file=file, start=1, end=5)
    callee = _method("helper", cls="Svc", file=file, start=10, end=12)
    tree = FactTree.from_facts(
        "r",
        [
            _symbol_method(name="do", cls="Svc", file=file, line=1),
            _symbol_method(name="helper", cls="Svc", file=file, line=10),
            _call(file, line=3, callee="self.helper"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee])
    by_name = {a.name: a for a in out.artifacts}
    assert by_name["do"].calls == (callee.id,)


def test_self_call_does_not_cross_classes() -> None:
    """`self.x()` from a method on A must not bind to a method named `x` on B."""
    file = "src/svc.py"
    a_do = _method("do", cls="A", file=file, start=1, end=5)
    b_x = _method("x", cls="B", file=file, start=20, end=22)
    tree = FactTree.from_facts(
        "r",
        [
            _symbol_method(name="do", cls="A", file=file, line=1),
            _symbol_method(name="x", cls="B", file=file, line=20),
            _call(file, line=3, callee="self.x"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[a_do, b_x])
    by_name = {a.name: a for a in out.artifacts}
    assert by_name["do"].calls == ()


def test_typed_parameter_resolves_to_class_method() -> None:
    """The FastAPI Depends pattern: `service: Svc` parameter, then
    `service.foo()` resolves to `Svc.foo` via the param's annotation."""
    router_file = "src/routers/users.py"
    svc_file = "src/services/svc.py"
    caller = _fn("get_user", file=router_file, start=5, end=10)
    callee = _method("foo", cls="Svc", file=svc_file, start=2, end=4)
    tree = FactTree.from_facts(
        "r",
        [
            _import_from(router_file, "src.services.svc", ["Svc"]),
            _symbol_fn(
                name="get_user",
                file=router_file,
                line=5,
                params=[("id", "int"), ("service", "Svc")],
            ),
            _symbol_method(name="foo", cls="Svc", file=svc_file, line=2),
            _call(router_file, line=7, callee="service.foo"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee])
    by_name = {a.name: a for a in out.artifacts}
    assert by_name["get_user"].calls == (callee.id,)


def test_typed_parameter_without_annotation_does_not_resolve() -> None:
    """Untyped local receivers stay unresolved — that's the bound on this
    fix and we want a failing case to lock it in."""
    file = "src/app.py"
    caller = _fn("handler", file=file, start=1, end=5)
    other = _method("foo", cls="Svc", file="src/svc.py", start=10, end=12)
    tree = FactTree.from_facts(
        "r",
        [
            _symbol_fn(
                name="handler",
                file=file,
                line=1,
                params=[("service", "")],  # no annotation
            ),
            _symbol_method(name="foo", cls="Svc", file="src/svc.py", line=10),
            _call(file, line=3, callee="service.foo"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, other])
    assert out.artifacts[0].calls == ()


def _assignment_module(
    *,
    file: str,
    target: str,
    source_kind: str,
    source: str,
    type_hint: str = "",
    repo: str = "r",
) -> Fact:
    return Fact(
        kind=FactKind.ASSIGNMENT,
        file=file,
        line=99,
        repo_id=repo,
        data={
            "target": target,
            "target_chain": [target],
            "source_kind": source_kind,
            "source": source,
            "type_hint": type_hint,
            "scope": "module",
        },
    )


def test_cross_file_module_singleton_resolves() -> None:
    """The fastapi-best-architecture pattern. A service module defines a
    module-level singleton (`user_service = UserService()`) and other
    modules import it directly. Calls on that imported name must resolve
    to the source class's methods.

    Without this, an entire layer of the architecture is invisible —
    every api -> service edge in a singleton-based codebase disappears."""
    api_file = "backend/app/admin/api/v1/sys/user.py"
    svc_file = "backend/app/admin/service/user_service.py"
    caller = _fn("get_userinfo", file=api_file, start=5, end=10)
    callee_method = _method(
        "get_userinfo_method", cls="UserService", file=svc_file, start=2, end=4
    )
    tree = FactTree.from_facts(
        "r",
        [
            _import_from(
                api_file, "backend.app.admin.service.user_service", ["user_service"]
            ),
            _symbol_fn(name="get_userinfo", file=api_file, line=5),
            _symbol_method(
                name="get_userinfo_method", cls="UserService", file=svc_file, line=2,
            ),
            _assignment_module(
                file=svc_file,
                target="user_service",
                source_kind="call",
                source="UserService",
            ),
            _call(api_file, line=7, callee="user_service.get_userinfo_method"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee_method])
    by_name = {a.name: a for a in out.artifacts}
    assert by_name["get_userinfo"].calls == (callee_method.id,)


def test_cross_file_singleton_with_type_hint_annotation() -> None:
    """`user_service: UserService = UserService()` — the type comes from the
    annotation, not the RHS source. Both shapes must work."""
    api_file = "src/api/user.py"
    svc_file = "src/service/svc.py"
    caller = _fn("handler", file=api_file, start=5, end=10)
    callee = _method("do", cls="UserService", file=svc_file, start=2, end=4)
    tree = FactTree.from_facts(
        "r",
        [
            _import_from(api_file, "src.service.svc", ["user_service"]),
            _symbol_fn(name="handler", file=api_file, line=5),
            _symbol_method(name="do", cls="UserService", file=svc_file, line=2),
            # type_hint set, source_kind="expr" (e.g. factory returns the type)
            _assignment_module(
                file=svc_file,
                target="user_service",
                source_kind="expr",
                source="",
                type_hint="UserService",
            ),
            _call(api_file, line=7, callee="user_service.do"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee])
    by_name = {a.name: a for a in out.artifacts}
    assert by_name["handler"].calls == (callee.id,)


def test_cross_file_singleton_does_not_match_unrelated_imports() -> None:
    """Negative case: if `user_service` isn't imported, don't resolve it.
    Guards against a too-greedy index lookup."""
    api_file = "src/api/user.py"
    svc_file = "src/service/svc.py"
    caller = _fn("handler", file=api_file, start=5, end=10)
    callee = _method("do", cls="UserService", file=svc_file, start=2, end=4)
    tree = FactTree.from_facts(
        "r",
        [
            # No import of `user_service` into api file.
            _symbol_fn(name="handler", file=api_file, line=5),
            _symbol_method(name="do", cls="UserService", file=svc_file, line=2),
            _assignment_module(
                file=svc_file,
                target="user_service",
                source_kind="call",
                source="UserService",
            ),
            _call(api_file, line=7, callee="user_service.do"),
        ],
    )
    out = FunctionCallResolver().resolve(tree=tree, artifacts=[caller, callee])
    assert out.artifacts[0].calls == ()


# ---- Annotated[T, Depends(fn)] type aliases ------------------------------


def _annotated_alias_assignment(
    *,
    file: str,
    target: str,
    line: int,
    repo: str = "r",
) -> Fact:
    """Module-level ASSIGNMENT fact matching `<target> = <some-subscript>`.
    The Depends() inside the subscript surfaces as separate CALL facts at
    the same (file, line), which is how the resolver correlates them."""
    return Fact(
        kind=FactKind.ASSIGNMENT,
        file=file,
        line=line,
        repo_id=repo,
        data={
            "target": target,
            "target_chain": [target],
            "source_kind": "expr",  # subscript -> "expr" per grammar
            "source": "",
            "type_hint": "",
            "scope": "module",
        },
    )


def _depends_call_at_module(
    *,
    file: str,
    line: int,
    name: str,
    repo: str = "r",
) -> Fact:
    return Fact(
        kind=FactKind.CALL,
        file=file,
        line=line,
        repo_id=repo,
        data={
            "callee": "Depends",
            "receiver": "",
            "method": "Depends",
            "args": [f"<name:{name}>"],
            "kwargs": {},
        },
    )


def test_annotated_alias_same_file_emits_dep_edge() -> None:
    """`SessionDep = Annotated[Session, Depends(get_db)]` in the same file as
    a handler that declares `db: SessionDep` should produce a CALLS edge
    `handler -> get_db`."""
    file = "src/app.py"
    handler = _fn("handler", file=file, start=10, end=20)
    get_db_art = _fn("get_db", file=file, start=1, end=3)
    tree = FactTree.from_facts(
        "r",
        [
            _annotated_alias_assignment(file=file, target="SessionDep", line=5),
            _depends_call_at_module(file=file, line=5, name="get_db"),
            _symbol_fn(name="get_db", file=file, line=1),
            _symbol_fn(
                name="handler",
                file=file,
                line=10,
                params=[("db", "SessionDep")],
            ),
        ],
    )
    out = FunctionCallResolver().resolve(
        tree=tree, artifacts=[handler, get_db_art]
    )
    by_name = {a.name: a for a in out.artifacts}
    assert get_db_art.id in by_name["handler"].calls


def test_annotated_alias_imported_from_deps_module() -> None:
    """The realistic pattern: a `deps.py` defines `SessionDep`, and route
    files import it. Resolver walks the import to find the alias on the
    other side."""
    deps_file = "src/api/deps.py"
    route_file = "src/api/routes/users.py"
    handler = _fn("read_user", file=route_file, start=5, end=10)
    get_db_art = _fn("get_db", file=deps_file, start=1, end=2)
    tree = FactTree.from_facts(
        "r",
        [
            _annotated_alias_assignment(file=deps_file, target="SessionDep", line=8),
            _depends_call_at_module(file=deps_file, line=8, name="get_db"),
            _symbol_fn(name="get_db", file=deps_file, line=1),
            _import_from(route_file, "src.api.deps", ["SessionDep"]),
            _symbol_fn(
                name="read_user",
                file=route_file,
                line=5,
                params=[("db", "SessionDep")],
            ),
        ],
    )
    out = FunctionCallResolver().resolve(
        tree=tree, artifacts=[handler, get_db_art]
    )
    by_name = {a.name: a for a in out.artifacts}
    assert get_db_art.id in by_name["read_user"].calls


def test_annotated_alias_with_multiple_depends_emits_one_edge_per_dep() -> None:
    """`Annotated[T, Depends(a), Depends(b)]` — a real pattern when an alias
    bundles auth + session in one type. Each Depends should produce a
    separate CALLS edge."""
    file = "src/app.py"
    handler = _fn("handler", file=file, start=15, end=20)
    a_art = _fn("auth", file=file, start=1, end=2)
    b_art = _fn("session", file=file, start=4, end=5)
    tree = FactTree.from_facts(
        "r",
        [
            _annotated_alias_assignment(file=file, target="Compound", line=8),
            _depends_call_at_module(file=file, line=8, name="auth"),
            _depends_call_at_module(file=file, line=8, name="session"),
            _symbol_fn(name="auth", file=file, line=1),
            _symbol_fn(name="session", file=file, line=4),
            _symbol_fn(
                name="handler",
                file=file,
                line=15,
                params=[("x", "Compound")],
            ),
        ],
    )
    out = FunctionCallResolver().resolve(
        tree=tree, artifacts=[handler, a_art, b_art]
    )
    by_name = {a.name: a for a in out.artifacts}
    assert a_art.id in by_name["handler"].calls
    assert b_art.id in by_name["handler"].calls


def test_unrelated_alias_without_depends_emits_no_edge() -> None:
    """Negative: if an alias has no Depends call, the resolver must not
    invent edges. Guards against the alias map matching by name alone."""
    file = "src/app.py"
    handler = _fn("handler", file=file, start=10, end=20)
    other_art = _fn("other", file=file, start=1, end=3)
    tree = FactTree.from_facts(
        "r",
        [
            # No Depends call at all — bare alias.
            _annotated_alias_assignment(file=file, target="JustAType", line=5),
            _symbol_fn(name="other", file=file, line=1),
            _symbol_fn(
                name="handler",
                file=file,
                line=10,
                params=[("x", "JustAType")],
            ),
        ],
    )
    out = FunctionCallResolver().resolve(
        tree=tree, artifacts=[handler, other_art]
    )
    assert out.artifacts[0].calls == ()
