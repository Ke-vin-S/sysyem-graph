"""CoverageResolver tests: test → CodeArtifact edges via imports."""

from __future__ import annotations

from datetime import datetime, timezone

from core.facts import Fact, FactKind, FactTree
from core.resolvers import CoverageResolver
from core.types import CodeArtifact, LineRange, TestCase, TestType

NOW = datetime(2026, 5, 16, tzinfo=timezone.utc)


def _test(name: str, *, file: str, repo_id: str = "r") -> TestCase:
    return TestCase(
        id=f"test:{repo_id}:{file}:{name}",
        repoId=repo_id,
        type=TestType.UNIT,
        name=name,
        file=file,
        lineRange=LineRange(start=1, end=1),
    )


def _artifact(name: str, *, file: str, type: str = "function", repo_id: str = "r") -> CodeArtifact:
    return CodeArtifact(
        id=f"fn:{repo_id}:{file}:{name}",
        repoId=repo_id,
        type=type,
        name=name,
        file=file,
        lineRange=LineRange(start=1, end=1),
        isPublic=True,
    )


def _import_fact(file: str, module: str, names: list[str]) -> Fact:
    return Fact(
        kind=FactKind.IMPORT,
        file=file,
        line=1,
        repo_id="r",
        data={"module": module, "names": names, "alias": ""},
    )


def test_from_x_import_y_links_to_artifact() -> None:
    test_file = "tests/test_charges.py"
    test = _test("test_get_charge", file=test_file)
    artifact = _artifact("get_charge", file="src/routers/charges.py")
    tree = FactTree.from_facts(
        "r", [_import_fact(test_file, "src.routers.charges", ["get_charge"])]
    )
    out = CoverageResolver().resolve(tree=tree, tests=[test], artifacts=[artifact])
    assert out.tests[0].covers_artifacts == (artifact.id,)
    assert len(out.edges) == 1
    assert "from src.routers.charges import get_charge" in out.edges[0].reason


def test_bare_import_covers_all_artifacts_in_module() -> None:
    test_file = "tests/test_x.py"
    test = _test("test_x", file=test_file)
    a1 = _artifact("foo", file="src/util.py")
    a2 = _artifact("bar", file="src/util.py")
    a3 = _artifact("baz", file="src/other.py")
    tree = FactTree.from_facts("r", [_import_fact(test_file, "src.util", [])])
    out = CoverageResolver().resolve(tree=tree, tests=[test], artifacts=[a1, a2, a3])
    covered = set(out.tests[0].covers_artifacts)
    assert a1.id in covered and a2.id in covered
    assert a3.id not in covered


def test_no_imports_leaves_covers_empty() -> None:
    test = _test("test_x", file="tests/test_x.py")
    tree = FactTree.from_facts("r", [])
    out = CoverageResolver().resolve(tree=tree, tests=[test], artifacts=[])
    assert out.tests[0].covers_artifacts == ()


def test_only_named_imports_match_by_name() -> None:
    test_file = "tests/test_x.py"
    test = _test("test_x", file=test_file)
    matched = _artifact("compute", file="src/util.py")
    unmatched = _artifact("private_helper", file="src/util.py")
    tree = FactTree.from_facts(
        "r", [_import_fact(test_file, "src.util", ["compute"])]
    )
    out = CoverageResolver().resolve(tree=tree, tests=[test], artifacts=[matched, unmatched])
    assert out.tests[0].covers_artifacts == (matched.id,)


def test_follows_reexport_through_init() -> None:
    """The Python `__init__.py` re-export pattern is everywhere. A test that
    writes `from core.resolvers import EndpointResolver` must resolve to
    the artifact in `core/resolvers/endpoint_resolver.py`, not get lost in
    the (empty) `__init__.py`."""
    test = _test("test_uses_it", file="tests/test_resolver.py")
    real_artifact = _artifact(
        "EndpointResolver",
        file="core/resolvers/endpoint_resolver.py",
        type="class",
    )
    tree = FactTree.from_facts(
        "r",
        [
            _import_fact("tests/test_resolver.py", "core.resolvers", ["EndpointResolver"]),
            _import_fact(
                "core/resolvers/__init__.py",
                "core.resolvers.endpoint_resolver",
                ["EndpointResolver"],
            ),
        ],
    )
    out = CoverageResolver().resolve(tree=tree, tests=[test], artifacts=[real_artifact])
    assert out.tests[0].covers_artifacts == (real_artifact.id,)
    assert "re-exported via core.resolvers" in out.edges[0].reason


def test_follows_chained_reexports() -> None:
    """pkg/__init__ re-exports from pkg.sub; pkg/sub/__init__ re-exports
    from pkg.sub.real. A test importing from pkg must still land on the
    real artifact."""
    test = _test("test_x", file="tests/x.py")
    real = _artifact("Thing", file="pkg/sub/real.py", type="class")
    tree = FactTree.from_facts(
        "r",
        [
            _import_fact("tests/x.py", "pkg", ["Thing"]),
            _import_fact("pkg/__init__.py", "pkg.sub", ["Thing"]),
            _import_fact("pkg/sub/__init__.py", "pkg.sub.real", ["Thing"]),
        ],
    )
    out = CoverageResolver().resolve(tree=tree, tests=[test], artifacts=[real])
    assert out.tests[0].covers_artifacts == (real.id,)


def test_relative_reexport_resolves() -> None:
    """`__init__.py` files often use `from .x import Y` relative imports.
    The resolver anchors those against the init's own package."""
    test = _test("test_x", file="tests/x.py")
    real = _artifact("Y", file="pkg/x.py")
    rel_import = Fact(
        kind=FactKind.IMPORT,
        file="pkg/__init__.py",
        line=1,
        repo_id="r",
        data={"module": "x", "names": ["Y"], "alias": "", "level": 1},
    )
    tree = FactTree.from_facts(
        "r",
        [_import_fact("tests/x.py", "pkg", ["Y"]), rel_import],
    )
    out = CoverageResolver().resolve(tree=tree, tests=[test], artifacts=[real])
    assert out.tests[0].covers_artifacts == (real.id,)


def _test_symbol(name: str, *, file: str, references: list[str]) -> Fact:
    """SYMBOL fact for a test function carrying its referenced-names set —
    triggers per-test tightening in CoverageResolver."""
    return Fact(
        kind=FactKind.SYMBOL,
        file=file,
        line=1,
        line_end=5,
        repo_id="r",
        data={
            "sym_kind": "function",
            "name": name,
            "is_async": False,
            "enclosing_class": "",
            "references": references,
        },
    )


def test_per_test_filter_drops_unreferenced_imports() -> None:
    """A test imports `create_charge` and `get_charge` from the same module
    but only references `get_charge` in its body. With per-test references
    available, only `get_charge` should get a COVERS edge."""
    test_file = "tests/test_x.py"
    test = _test("test_only_get", file=test_file)
    used = _artifact("get_charge", file="src/routers/charges.py")
    unused = _artifact("create_charge", file="src/routers/charges.py")
    tree = FactTree.from_facts(
        "r",
        [
            _import_fact(test_file, "src.routers.charges", ["get_charge", "create_charge"]),
            _test_symbol("test_only_get", file=test_file, references=["get_charge"]),
        ],
    )
    out = CoverageResolver().resolve(tree=tree, tests=[test], artifacts=[used, unused])
    assert out.tests[0].covers_artifacts == (used.id,)


def test_per_test_filter_drops_all_when_test_body_touches_neither() -> None:
    """Mirrors the `test_charge_id_is_string` case: imports both names but
    references neither. Coverage should be empty, not file-scoped."""
    test_file = "tests/test_x.py"
    test = _test("test_only_literal", file=test_file)
    a1 = _artifact("get_charge", file="src/routers/charges.py")
    a2 = _artifact("create_charge", file="src/routers/charges.py")
    tree = FactTree.from_facts(
        "r",
        [
            _import_fact(test_file, "src.routers.charges", ["get_charge", "create_charge"]),
            # references is empty: the test only does `assert isinstance("abc", str)`.
            _test_symbol("test_only_literal", file=test_file, references=["isinstance", "str"]),
        ],
    )
    out = CoverageResolver().resolve(tree=tree, tests=[test], artifacts=[a1, a2])
    assert out.tests[0].covers_artifacts == ()


def test_no_references_fact_keeps_file_scoped_fallback() -> None:
    """When the grammar didn't emit `references` (e.g. Java tests today,
    or any tree with no SYMBOL facts), behavior must fall back to
    file-scoped coverage so existing edges don't vanish."""
    test_file = "tests/test_x.py"
    test = _test("test_it", file=test_file)
    artifact = _artifact("Bar", file="src/util.py", type="function")
    tree = FactTree.from_facts(
        "r",
        # IMPORT fact only; no SYMBOL fact for the test — the references
        # index is empty, so the resolver falls back to file-scoped coverage.
        [_import_fact(test_file, "src.util", ["Bar"])],
    )
    out = CoverageResolver().resolve(tree=tree, tests=[test], artifacts=[artifact])
    assert out.tests[0].covers_artifacts == (artifact.id,)


def test_repo_root_relativizes_import_facts() -> None:
    """IMPORT facts walked under a temp dir have absolute paths; tests
    have repo-relative paths. The resolver bridges them via repo_root."""
    test = _test("test_x", file="tests/test_x.py")
    artifact = _artifact("foo", file="src/util.py")
    abs_test_path = "/tmp/repo/tests/test_x.py"
    tree = FactTree.from_facts(
        "r", [_import_fact(abs_test_path, "src.util", ["foo"])]
    )
    out = CoverageResolver().resolve(
        tree=tree, tests=[test], artifacts=[artifact], repo_root="/tmp/repo"
    )
    assert out.tests[0].covers_artifacts == (artifact.id,)
