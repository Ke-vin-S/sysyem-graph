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
