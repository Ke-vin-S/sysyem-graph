"""ConfigGrammar -> CONFIG_VALUE fact tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.facts import FactKind
from ingestion.grammars import ConfigGrammar


@pytest.fixture
def grammar() -> ConfigGrammar:
    return ConfigGrammar()


APPLICATION_YML = """\
server:
  servlet:
    context-path: /v1
spring:
  application:
    name: payment-service
"""


def test_yaml_flattens_to_dotted_keys(grammar: ConfigGrammar) -> None:
    facts = grammar.extract(Path("application.yml"), APPLICATION_YML, repo_id="r")
    by_key = {f.data["key"]: f.data["value"] for f in facts if f.kind is FactKind.CONFIG_VALUE}
    assert by_key["server.servlet.context-path"] == "/v1"
    assert by_key["spring.application.name"] == "payment-service"


def test_toml_supported(grammar: ConfigGrammar) -> None:
    toml = """\
[tool.poetry]
name = "x"
version = "0.1.0"
"""
    facts = grammar.extract(Path("pyproject.toml"), toml, repo_id="r")
    keys = {f.data["key"] for f in facts}
    assert "tool.poetry.name" in keys
    assert "tool.poetry.version" in keys


def test_properties_file(grammar: ConfigGrammar) -> None:
    props = """\
# this is a comment
server.port=8080
spring.datasource.url:jdbc:postgresql://db/x
"""
    facts = grammar.extract(Path("application.properties"), props, repo_id="r")
    by_key = {f.data["key"]: f.data["value"] for f in facts}
    assert by_key["server.port"] == "8080"
    assert by_key["spring.datasource.url"] == "jdbc:postgresql://db/x"


def test_env_file(grammar: ConfigGrammar) -> None:
    env = """\
# comment
DB_URL="postgres://x"
LOG_LEVEL=info
"""
    facts = grammar.extract(Path(".env"), env, repo_id="r")
    by_key = {f.data["key"]: f.data["value"] for f in facts}
    assert by_key["DB_URL"] == "postgres://x"
    assert by_key["LOG_LEVEL"] == "info"


def test_invalid_yaml_returns_empty(grammar: ConfigGrammar) -> None:
    assert grammar.extract(Path("bad.yaml"), ": : not valid", repo_id="r") == []
