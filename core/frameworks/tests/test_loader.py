"""Tests for the framework loader, detector, and overlay composition."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.facts import Fact, FactKind, FactTree
from core.frameworks import (
    EffectiveFramework,
    FrameworkLibrary,
    RepoOverlay,
    compose,
    detect_frameworks,
    load_library,
)
from core.frameworks.library import DEFAULT_FRAMEWORKS_DIR
from core.types.errors import ConfigurationError


def test_load_library_picks_up_all_shipped_yamls() -> None:
    library = load_library(DEFAULT_FRAMEWORKS_DIR)
    names = library.names()
    assert set(names) == {
        "python",
        "java",
        "fastapi",
        "flask",
        "spring",
        "pytest",
        "pydantic",
        "sqlalchemy",
        "kafka_python",
        "jpa",
        "spring_kafka",
        "mockito",
    }


def test_load_library_python_definition_shape() -> None:
    library = load_library(DEFAULT_FRAMEWORKS_DIR)
    python = library.get("python")
    assert python.language == "python"
    # `test_` prefix moved to core/languages/python/profile.yaml; framework YAML only
    # carries framework-specific test config now (empty for default Python).
    assert python.http_clients is not None
    assert "httpx" in python.http_clients.external_modules


def test_load_library_fastapi_routes_shape() -> None:
    library = load_library(DEFAULT_FRAMEWORKS_DIR)
    fastapi = library.get("fastapi")
    assert fastapi.routes is not None
    assert any("get" in p for p in fastapi.routes.decorator_callee_patterns)
    assert fastapi.routes.mount_calls
    assert fastapi.routes.mount_calls[0].method == "include_router"


def test_load_library_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_library(tmp_path / "nope")


def test_load_library_invalid_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(": : not valid")
    with pytest.raises(ConfigurationError):
        load_library(tmp_path)


def test_load_library_duplicate_names_raises(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("name: dup\nlanguage: python\n")
    (tmp_path / "b.yaml").write_text("name: dup\nlanguage: python\n")
    with pytest.raises(ConfigurationError):
        load_library(tmp_path)


def _fact(kind: FactKind, **data) -> Fact:
    return Fact(kind=kind, file="x.py", line=1, repo_id="r", data=data)


def test_detect_frameworks_by_import() -> None:
    library = load_library(DEFAULT_FRAMEWORKS_DIR)
    tree = FactTree.from_facts(
        "r",
        [
            _fact(FactKind.IMPORT, module="fastapi", names=["FastAPI"]),
            _fact(FactKind.IMPORT, module="pytest", names=[]),
        ],
    )
    detected = {fw.name for fw in detect_frameworks(tree, library)}
    assert "fastapi" in detected
    assert "pytest" in detected
    # Flask should NOT activate without a flask import.
    assert "flask" not in detected


def test_detect_frameworks_by_config_key() -> None:
    library = load_library(DEFAULT_FRAMEWORKS_DIR)
    tree = FactTree.from_facts(
        "r",
        [
            Fact(
                kind=FactKind.CONFIG_VALUE,
                file="application.yml",
                line=1,
                repo_id="r",
                data={"key": "server.servlet.context-path", "value": "/v1"},
            )
        ],
    )
    detected = {fw.name for fw in detect_frameworks(tree, library)}
    assert "spring" in detected


def test_compose_without_overlay_is_passthrough() -> None:
    library = load_library(DEFAULT_FRAMEWORKS_DIR)
    python = library.get("python")
    effective = compose(python, None)
    assert isinstance(effective, EffectiveFramework)
    assert effective.http_clients is not None
    assert "httpx" in effective.http_clients.external_modules


def test_compose_adds_overlay_modules_and_removes_internal_wrappers() -> None:
    library = load_library(DEFAULT_FRAMEWORKS_DIR)
    python = library.get("python")
    overlay = RepoOverlay(
        repo_id="r",
        external_modules=("acme.payment_client", "httpx"),
        internal_test_wrappers=("psycopg",),  # claims psycopg is just an in-house wrapper
    )
    effective = compose(python, overlay)
    assert effective.http_clients is not None
    modules = effective.http_clients.external_modules
    assert "acme.payment_client" in modules
    assert "psycopg" not in modules  # removed by internal_test_wrappers


def test_library_for_language_filter() -> None:
    library = load_library(DEFAULT_FRAMEWORKS_DIR)
    python_frameworks = {fw.name for fw in library.for_language("python")}
    assert "python" in python_frameworks
    assert "fastapi" in python_frameworks
    assert "flask" in python_frameworks
    assert "java" not in python_frameworks
