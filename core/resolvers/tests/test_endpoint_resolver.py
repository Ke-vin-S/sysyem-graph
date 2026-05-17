"""End-to-end tests for the EndpointResolver: facts -> full HTTP paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.frameworks import compose, detect_frameworks, load_library
from core.frameworks.library import DEFAULT_FRAMEWORKS_DIR
from core.resolvers import EndpointResolver, ResolverContext
from core.walker import Walker


@pytest.fixture(scope="module")
def library():
    return load_library(DEFAULT_FRAMEWORKS_DIR)


def _resolve(tmp_path: Path, library) -> list:
    walker = Walker()
    tree = walker.walk(tmp_path, repo_id="r")
    detected = detect_frameworks(tree, library)
    effective = tuple(compose(fw, None) for fw in detected)
    return EndpointResolver().resolve(ResolverContext(tree=tree, frameworks=effective, repo_id="r"))


# 1. FastAPI with root_path + include_router prefix + @router.get
def test_fastapi_full_prefix_chain(tmp_path: Path, library) -> None:
    (tmp_path / "main.py").write_text(
        """\
from fastapi import FastAPI, APIRouter

app = FastAPI(root_path="/v1")
router = APIRouter()


@router.get("/{id}")
def get_charge(id):
    return {}


app.include_router(router, prefix="/payments")
"""
    )
    endpoints = _resolve(tmp_path, library)
    assert any(e.method == "GET" and e.full_path == "/v1/payments/{id}" for e in endpoints), (
        [(e.method, e.full_path) for e in endpoints]
    )


# 2. FastAPI with no prefix chain
def test_fastapi_no_prefix(tmp_path: Path, library) -> None:
    (tmp_path / "main.py").write_text(
        """\
from fastapi import FastAPI

app = FastAPI()


@app.get("/users/{id}")
def get_user(id):
    return {}
"""
    )
    endpoints = _resolve(tmp_path, library)
    matching = [e for e in endpoints if e.method == "GET"]
    assert any(e.full_path == "/users/{id}" for e in matching), [(e.method, e.full_path) for e in matching]


# 3. Flask register_blueprint prefix
def test_flask_blueprint_prefix(tmp_path: Path, library) -> None:
    (tmp_path / "main.py").write_text(
        """\
from flask import Flask, Blueprint

app = Flask(__name__)
bp = Blueprint("api", __name__)


@bp.route("/health")
def health():
    return {}


app.register_blueprint(bp, url_prefix="/api")
"""
    )
    endpoints = _resolve(tmp_path, library)
    paths = {(e.method, e.full_path) for e in endpoints}
    # The route decorator method is "route" — we accept it even though method
    # name isn't an HTTP verb; the resolver returns it uppercased.
    assert ("ROUTE", "/api/health") in paths or ("GET", "/api/health") in paths, paths


# 4. Spring controller with context-path
def test_spring_controller_with_context_path(tmp_path: Path, library) -> None:
    (tmp_path / "src" / "main" / "resources").mkdir(parents=True)
    (tmp_path / "src" / "main" / "resources" / "application.yml").write_text(
        "server:\n  servlet:\n    context-path: /v2\n"
    )
    (tmp_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
    (tmp_path / "src" / "main" / "java" / "com" / "example" / "UserController.java").write_text(
        """\
package com.example;

import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.GetMapping;

@RestController
@RequestMapping("/users")
public class UserController {
    @GetMapping("/{id}")
    public String getUser(String id) {
        return "ok";
    }
}
"""
    )
    endpoints = _resolve(tmp_path, library)
    paths = {(e.method, e.full_path) for e in endpoints}
    assert ("GET", "/v2/users/{id}") in paths, paths


# 5. Spring controller without context-path
def test_spring_controller_no_context_path(tmp_path: Path, library) -> None:
    (tmp_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
    (tmp_path / "src" / "main" / "java" / "com" / "example" / "UserController.java").write_text(
        """\
package com.example;

import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.GetMapping;

@RestController
@RequestMapping("/users")
public class UserController {
    @GetMapping("/{id}")
    public String getUser(String id) { return ""; }
}
"""
    )
    endpoints = _resolve(tmp_path, library)
    paths = {(e.method, e.full_path) for e in endpoints}
    assert ("GET", "/users/{id}") in paths, paths


# 6. FastAPI + Flask imports in the same file produces two candidates with
#    derivation receipts. (We don't pick a winner; downstream uses confidence.)
def test_mixed_framework_imports_emits_both(tmp_path: Path, library) -> None:
    (tmp_path / "main.py").write_text(
        """\
from fastapi import FastAPI
from flask import Flask

app = FastAPI()


@app.get("/health")
def health_a():
    return {}


@app.route("/healthz")
def health_b():
    return {}
"""
    )
    endpoints = _resolve(tmp_path, library)
    by_framework = {e.framework for e in endpoints}
    assert {"fastapi", "flask"} <= by_framework
    # Every endpoint has at least one fact in its derivation.
    assert all(e.derivation for e in endpoints)


def test_fastapi_multi_file_disambiguates_routers(tmp_path: Path, library) -> None:
    """Two routers with the same variable name `router` in different files
    must not collide. `app.include_router(charges.router, prefix="/payments")`
    in main.py + `router = APIRouter()` + `@router.get(...)` in
    routers/charges.py should resolve to `/payments/...`, not `/...`."""
    (tmp_path / "src" / "routers").mkdir(parents=True)
    (tmp_path / "src" / "routers" / "__init__.py").write_text("")
    (tmp_path / "src" / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "from .routers import charges, health\n"
        "app = FastAPI()\n"
        "app.include_router(charges.router, prefix='/payments')\n"
        "app.include_router(health.router)\n"
    )
    (tmp_path / "src" / "routers" / "charges.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/x')\n"
        "def get_x(): return {}\n"
    )
    (tmp_path / "src" / "routers" / "health.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/healthz')\n"
        "def healthz(): return {}\n"
    )
    endpoints = _resolve(tmp_path, library)
    paths = {(e.method, e.full_path) for e in endpoints}
    assert ("GET", "/payments/x") in paths, paths
    assert ("GET", "/healthz") in paths, paths


def test_derivation_contains_dec_fact_id(tmp_path: Path, library) -> None:
    (tmp_path / "main.py").write_text(
        """\
from fastapi import FastAPI

app = FastAPI(root_path="/v1")


@app.get("/u")
def u():
    return {}
"""
    )
    endpoints = _resolve(tmp_path, library)
    target = next(e for e in endpoints if e.full_path == "/v1/u")
    # Three contributing facts: FastAPI(root_path) call, decorator, handler symbol.
    assert len(target.derivation) >= 2
    assert all(d.startswith("fact:") for d in target.derivation)
