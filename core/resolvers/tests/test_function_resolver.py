"""FunctionResolver tests."""

from __future__ import annotations

from pathlib import Path

from core.frameworks import compose, detect_frameworks, load_library
from core.frameworks.library import DEFAULT_FRAMEWORKS_DIR
from core.resolvers import FunctionResolver, ResolverContext
from core.walker import Walker


def _context(tmp_path: Path):
    tree = Walker().walk(tmp_path, repo_id="r")
    library = load_library(DEFAULT_FRAMEWORKS_DIR)
    detected = detect_frameworks(tree, library)
    effective = tuple(compose(fw, None) for fw in detected)
    return ResolverContext(tree=tree, frameworks=effective, repo_id="r")


def test_emits_public_function_artifact(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text(
        "def compute_fee(amount):\n    return amount * 0.03\n"
        "def _internal():\n    return 1\n"
    )
    artifacts = FunctionResolver().resolve(_context(tmp_path))
    names_public = {(a.name, a.is_public, a.type) for a in artifacts}
    assert ("compute_fee", True, "function") in names_public
    # Private function present but marked is_public=False
    assert any(name == "_internal" and not is_public for name, is_public, _ in names_public)


def test_emits_class_and_method_artifacts(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text(
        "class UserService:\n"
        "    def lookup(self, id):\n"
        "        return id\n"
        "    def _cache(self):\n"
        "        return None\n"
    )
    artifacts = FunctionResolver().resolve(_context(tmp_path))
    types = {(a.name, a.type) for a in artifacts}
    assert ("UserService", "class") in types
    assert ("lookup", "method") in types
    assert ("_cache", "method") in types

    # Method ID embeds the enclosing class so identical method names on
    # different classes can't collide.
    lookup_id = next(a.id for a in artifacts if a.name == "lookup")
    assert "UserService.lookup" in lookup_id


def test_excludes_test_paths(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_ok(): pass\n")
    (tmp_path / "src.py").write_text("def real(): pass\n")
    artifacts = FunctionResolver().resolve(_context(tmp_path))
    names = {a.name for a in artifacts}
    assert "real" in names
    assert "test_ok" not in names


def test_dedupes_by_id(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("def foo(): pass\n")
    artifacts = FunctionResolver().resolve(_context(tmp_path))
    ids = [a.id for a in artifacts]
    assert len(ids) == len(set(ids))
