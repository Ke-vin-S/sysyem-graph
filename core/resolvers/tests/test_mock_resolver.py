"""MockResolver tests."""

from __future__ import annotations

from core.facts import Fact, FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import MockPatterns
from core.resolvers import MockResolver
from core.types import CodeArtifact, LineRange, MockKind, TestCase, TestType


def _test(name: str, *, file: str, repo: str = "r") -> TestCase:
    return TestCase(
        id=f"test:{repo}:{file}:{name}",
        repoId=repo,
        type=TestType.UNIT,
        name=name,
        file=file,
        lineRange=LineRange(start=1, end=10),
    )


def _fn(name: str, *, file: str, repo: str = "r") -> CodeArtifact:
    return CodeArtifact(
        id=f"fn:{repo}:{file}:{name}",
        repoId=repo,
        type="function",
        name=name,
        file=file,
        lineRange=LineRange(start=1, end=3),
        isPublic=True,
    )


def _method(class_name: str, name: str, *, file: str, repo: str = "r") -> CodeArtifact:
    return CodeArtifact(
        id=f"method:{repo}:{file}:{class_name}.{name}",
        repoId=repo,
        type="method",
        name=name,
        file=file,
        lineRange=LineRange(start=1, end=3),
        isPublic=True,
    )


def _decorator(file: str, line: int, *, callee: str, args: list, target_symbol: str) -> Fact:
    return Fact(
        kind=FactKind.DECORATOR,
        file=file,
        line=line,
        repo_id="r",
        data={
            "callee": callee,
            "args": args,
            "kwargs": {},
            "target_symbol": target_symbol,
            "target_line": line + 1,
        },
    )


def _frameworks() -> tuple[EffectiveFramework, ...]:
    """A single fake framework whose YAML declares mock callees."""
    return (
        EffectiveFramework(
            name="python",
            language="python",
            routes=None,
            tests=None,
            mocks=MockPatterns(
                decorator_callees=("patch", "patch.object", "mock.patch"),
            ),
            http_clients=None,
        ),
    )


def test_patch_string_resolves_to_local_artifact() -> None:
    test_file = "tests/test_x.py"
    test = _test("test_get", file=test_file)
    target_fn = _fn("get_charge", file="src/routers/charges.py")
    tree = FactTree.from_facts(
        "r",
        [
            _decorator(test_file, 5, callee="patch", args=["src.routers.charges.get_charge"], target_symbol="test_get"),
        ],
    )
    out = MockResolver().resolve(
        tree=tree, tests=[test], artifacts=[target_fn],
        frameworks=_frameworks(), repo_id="r",
    )
    assert len(out.mocks) == 1
    m = out.mocks[0]
    assert m.kind is MockKind.PATCH_STR
    assert m.patch_target == "src.routers.charges.get_charge"
    assert m.target_artifact_id == target_fn.id
    assert m.test_id == test.id


def test_patch_string_with_third_party_target_leaves_artifact_id_none() -> None:
    test_file = "tests/test_x.py"
    test = _test("test_x", file=test_file)
    tree = FactTree.from_facts(
        "r",
        [_decorator(test_file, 5, callee="patch", args=["httpx.get"], target_symbol="test_x")],
    )
    out = MockResolver().resolve(
        tree=tree, tests=[test], artifacts=[],
        frameworks=_frameworks(), repo_id="r",
    )
    assert len(out.mocks) == 1
    m = out.mocks[0]
    assert m.patch_target == "httpx.get"
    assert m.target_artifact_id is None
    assert m.kind is MockKind.PATCH_STR


def test_patch_object_resolves_method_via_class_qualifier() -> None:
    test_file = "tests/test_x.py"
    test = _test("test_m", file=test_file)
    target_method = _method("MyClass", "do_thing", file="src/mod.py")
    tree = FactTree.from_facts(
        "r",
        [
            _decorator(
                test_file, 5,
                callee="patch.object",
                args=["<name:MyClass>", "do_thing"],
                target_symbol="test_m",
            )
        ],
    )
    out = MockResolver().resolve(
        tree=tree, tests=[test], artifacts=[target_method],
        frameworks=_frameworks(), repo_id="r",
    )
    assert len(out.mocks) == 1
    m = out.mocks[0]
    assert m.kind is MockKind.PATCH_OBJECT
    assert m.patch_target == "MyClass.do_thing"
    assert m.target_artifact_id == target_method.id


def test_decorator_on_non_test_function_is_skipped() -> None:
    """A @patch on a regular helper (not in the tests list) shouldn't
    produce a Mock — only test functions count."""
    tree = FactTree.from_facts(
        "r",
        [_decorator("src/x.py", 5, callee="patch", args=["other"], target_symbol="some_helper")],
    )
    out = MockResolver().resolve(
        tree=tree, tests=[], artifacts=[],
        frameworks=_frameworks(), repo_id="r",
    )
    assert out.mocks == []


def test_unknown_callee_is_ignored() -> None:
    test_file = "tests/test_x.py"
    test = _test("test_x", file=test_file)
    tree = FactTree.from_facts(
        "r",
        # callee 'pytest.fixture' isn't in our mocks list
        [_decorator(test_file, 5, callee="pytest.fixture", args=[], target_symbol="test_x")],
    )
    out = MockResolver().resolve(
        tree=tree, tests=[test], artifacts=[],
        frameworks=_frameworks(), repo_id="r",
    )
    assert out.mocks == []


def test_no_mocks_in_yaml_returns_empty() -> None:
    """Empty mocks.decorator_callees → resolver returns immediately."""
    test_file = "tests/test_x.py"
    test = _test("test_x", file=test_file)
    tree = FactTree.from_facts(
        "r", [_decorator(test_file, 5, callee="patch", args=["httpx.get"], target_symbol="test_x")]
    )
    empty_fw = (
        EffectiveFramework(name="python", language="python", routes=None, tests=None, mocks=None, http_clients=None),
    )
    out = MockResolver().resolve(
        tree=tree, tests=[test], artifacts=[],
        frameworks=empty_fw, repo_id="r",
    )
    assert out.mocks == []


def test_duplicate_patches_deduped_by_id() -> None:
    test_file = "tests/test_x.py"
    test = _test("test_x", file=test_file)
    tree = FactTree.from_facts(
        "r",
        [
            _decorator(test_file, 5, callee="patch", args=["httpx.get"], target_symbol="test_x"),
            _decorator(test_file, 6, callee="patch", args=["httpx.get"], target_symbol="test_x"),
        ],
    )
    out = MockResolver().resolve(
        tree=tree, tests=[test], artifacts=[],
        frameworks=_frameworks(), repo_id="r",
    )
    assert len(out.mocks) == 1
