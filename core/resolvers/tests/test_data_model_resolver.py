"""DataModelResolver tests."""

from __future__ import annotations

from core.facts import Fact, FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import DataModelPatterns
from core.resolvers import DataModelResolver
from core.types import DataModelKind


def _class_fact(name: str, *, file: str, bases: list[str], line: int = 1) -> Fact:
    return Fact(
        kind=FactKind.CLASS_DEF,
        file=file,
        line=line,
        line_end=line + 5,
        repo_id="r",
        data={"name": name, "bases": bases, "enclosing_class": ""},
    )


def _decorator(*, file: str, callee: str, target: str, line: int = 1) -> Fact:
    return Fact(
        kind=FactKind.DECORATOR,
        file=file,
        line=line,
        repo_id="r",
        data={"callee": callee, "args": [], "kwargs": {}, "target_symbol": target, "target_line": line + 1},
    )


def _fw(name: str, dm: DataModelPatterns) -> EffectiveFramework:
    return EffectiveFramework(
        name=name, language="python", routes=None, tests=None, mocks=None,
        http_clients=None, data_models=dm, queries=None, kafka=None,
    )


def test_pydantic_base_class_match() -> None:
    fw = _fw("pydantic", DataModelPatterns(kind="pydantic", base_class_suffixes=("BaseModel",)))
    tree = FactTree.from_facts(
        "r", [_class_fact("Charge", file="src/models.py", bases=["pydantic.BaseModel"])]
    )
    out = DataModelResolver().resolve(tree=tree, frameworks=(fw,), repo_id="r")
    assert len(out.data_models) == 1
    assert out.data_models[0].name == "Charge"
    assert out.data_models[0].kind is DataModelKind.PYDANTIC


def test_dataclass_decorator_match() -> None:
    fw = _fw("python", DataModelPatterns(kind="dataclass", decorator_callees=("dataclass",)))
    tree = FactTree.from_facts(
        "r",
        [
            _class_fact("Order", file="src/m.py", bases=[]),
            _decorator(file="src/m.py", callee="dataclass", target="Order"),
        ],
    )
    out = DataModelResolver().resolve(tree=tree, frameworks=(fw,), repo_id="r")
    assert len(out.data_models) == 1
    assert out.data_models[0].kind is DataModelKind.DATACLASS


def test_sqlalchemy_base_suffix_match_by_last_segment() -> None:
    fw = _fw("sqlalchemy", DataModelPatterns(kind="sqlalchemy_orm", base_class_suffixes=("Base",)))
    tree = FactTree.from_facts(
        "r", [_class_fact("User", file="src/entity.py", bases=["app.db.Base"])]
    )
    out = DataModelResolver().resolve(tree=tree, frameworks=(fw,), repo_id="r")
    assert len(out.data_models) == 1
    assert out.data_models[0].kind is DataModelKind.SQLALCHEMY_ORM


def test_non_matching_class_emits_no_model() -> None:
    fw = _fw("pydantic", DataModelPatterns(kind="pydantic", base_class_suffixes=("BaseModel",)))
    tree = FactTree.from_facts(
        "r", [_class_fact("Plain", file="src/m.py", bases=["object"])]
    )
    out = DataModelResolver().resolve(tree=tree, frameworks=(fw,), repo_id="r")
    assert out.data_models == []


def test_first_framework_wins_on_multi_match() -> None:
    """Class matches pydantic AND dataclass; first declared framework wins."""
    pyd = _fw("pydantic", DataModelPatterns(kind="pydantic", base_class_suffixes=("BaseModel",)))
    dc = _fw("python", DataModelPatterns(kind="dataclass", decorator_callees=("dataclass",)))
    tree = FactTree.from_facts(
        "r",
        [
            _class_fact("Hybrid", file="src/m.py", bases=["BaseModel"]),
            _decorator(file="src/m.py", callee="dataclass", target="Hybrid"),
        ],
    )
    out = DataModelResolver().resolve(tree=tree, frameworks=(pyd, dc), repo_id="r")
    assert len(out.data_models) == 1
    assert out.data_models[0].kind is DataModelKind.PYDANTIC


def test_java_entity_annotation_match() -> None:
    """A class with `@Entity` (Java) should match annotation_callees."""
    fw = _fw("jpa", DataModelPatterns(kind="jpa_entity", annotation_callees=("Entity",)))
    ann = Fact(
        kind=FactKind.ANNOTATION, file="src/User.java", line=1, repo_id="r",
        data={"callee": "Entity", "args": [], "kwargs": {}, "target_symbol": "User",
              "target_kind": "class", "qualified": "javax.persistence.Entity"},
    )
    tree = FactTree.from_facts(
        "r", [_class_fact("User", file="src/User.java", bases=[]), ann],
    )
    out = DataModelResolver().resolve(tree=tree, frameworks=(fw,), repo_id="r")
    assert len(out.data_models) == 1
    assert out.data_models[0].kind.value == "jpa_entity"


def test_no_data_models_patterns_returns_empty() -> None:
    fw = EffectiveFramework(
        name="other", language="python", routes=None, tests=None, mocks=None,
        http_clients=None, data_models=None, queries=None, kafka=None,
    )
    tree = FactTree.from_facts("r", [_class_fact("X", file="src/x.py", bases=["BaseModel"])])
    out = DataModelResolver().resolve(tree=tree, frameworks=(fw,), repo_id="r")
    assert out.data_models == []
