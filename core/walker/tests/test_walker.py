"""Walker tests."""

from __future__ import annotations

from pathlib import Path

from core.facts import FactKind
from core.walker import Walker, WalkerConfig


def test_walker_dispatches_to_grammars(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def f(): pass\n")
    (tmp_path / "App.java").write_text(
        "package com.x; public class App { public void m() {} }"
    )
    (tmp_path / "application.yml").write_text("server:\n  port: 8080\n")
    (tmp_path / "ignore.md").write_text("# readme")

    tree = Walker().walk(tmp_path, repo_id="r")
    files_with_facts = set(tree.files())
    assert any(f.endswith("main.py") for f in files_with_facts)
    assert any(f.endswith("App.java") for f in files_with_facts)
    assert any(f.endswith("application.yml") for f in files_with_facts)
    assert all(not f.endswith("ignore.md") for f in files_with_facts)


def test_walker_skips_excluded_dirs(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "x.py").write_text("def x(): pass")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "y.py").write_text("def y(): pass")

    tree = Walker().walk(tmp_path, repo_id="r")
    files = {Path(f).name for f in tree.files()}
    assert "y.py" in files
    assert "x.py" not in files


def test_walker_respects_size_cap(tmp_path: Path) -> None:
    big = tmp_path / "huge.py"
    big.write_text("# " + "a" * 200)
    walker = Walker(config=WalkerConfig(max_file_bytes=50))
    tree = walker.walk(tmp_path, repo_id="r")
    assert tree.files() == []


def test_walker_collects_config_values(tmp_path: Path) -> None:
    (tmp_path / "application.yml").write_text(
        "server:\n  servlet:\n    context-path: /v1\n"
    )
    tree = Walker().walk(tmp_path, repo_id="r")
    keys = {f.data["key"] for f in tree.where(kind=FactKind.CONFIG_VALUE)}
    assert "server.servlet.context-path" in keys


def test_walker_missing_root_is_empty(tmp_path: Path) -> None:
    tree = Walker().walk(tmp_path / "does-not-exist", repo_id="r")
    assert len(tree) == 0


def test_walker_single_file_input(tmp_path: Path) -> None:
    file = tmp_path / "one.py"
    file.write_text("def f(): pass\n")
    tree = Walker().walk(file, repo_id="r")
    assert any(f.endswith("one.py") for f in tree.files())
