"""Configuration files -> CONFIG_VALUE facts.

Supports `.yaml`/`.yml` (PyYAML), `.toml` (stdlib `tomllib`), `.properties`
(Java/Spring), `.env` (KEY=VALUE), and JSON. Each leaf value becomes one
fact with a dotted-path key (`server.servlet.context-path`).

Resolvers use these facts to find route base paths, datasource URLs, message
queue topics — anything declared once in config and referenced elsewhere.
"""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path
from typing import Any

import yaml

from core.facts import Fact, FactKind
from ingestion.grammars.grammar import Grammar

logger = logging.getLogger(__name__)

# Cap on the number of facts a single config file can produce. Some YAMLs
# (e.g. CI configs) are enormous and would explode the fact tree if walked
# blindly.
_FACT_LIMIT_PER_FILE = 1000


class ConfigGrammar(Grammar):
    suffixes = (".yaml", ".yml", ".toml", ".properties", ".env", ".json")

    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        # `Path(".env").suffix` is "" because pathlib treats leading-dot
        # filenames as having no extension. Treat them as `.env` explicitly.
        suffix = file.suffix.lower() or (".env" if file.name == ".env" else "")
        try:
            if suffix in (".yaml", ".yml"):
                data = yaml.safe_load(content)
                return self._from_mapping(data, file=str(file), repo_id=repo_id)
            if suffix == ".toml":
                data = tomllib.loads(content)
                return self._from_mapping(data, file=str(file), repo_id=repo_id)
            if suffix == ".json":
                data = json.loads(content)
                return self._from_mapping(data, file=str(file), repo_id=repo_id)
            if suffix == ".properties":
                return self._from_properties(content, file=str(file), repo_id=repo_id)
            if suffix == ".env":
                return self._from_env(content, file=str(file), repo_id=repo_id)
        except (yaml.YAMLError, tomllib.TOMLDecodeError, json.JSONDecodeError, OSError):
            logger.warning("config_grammar: failed to parse %s", file)
            return []
        return []

    def _from_mapping(self, data: Any, *, file: str, repo_id: str) -> list[Fact]:
        if not isinstance(data, dict):
            return []
        facts: list[Fact] = []
        for key, value in _flatten(data, prefix=""):
            facts.append(
                Fact(
                    kind=FactKind.CONFIG_VALUE,
                    file=file,
                    line=1,
                    repo_id=repo_id,
                    data={"key": key, "value": _stringify(value), "format": Path(file).suffix.lstrip(".")},
                )
            )
            if len(facts) >= _FACT_LIMIT_PER_FILE:
                break
        return facts

    def _from_properties(self, content: str, *, file: str, repo_id: str) -> list[Fact]:
        facts: list[Fact] = []
        for lineno, raw in enumerate(content.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith(("#", "!")):
                continue
            if "=" not in line and ":" not in line:
                continue
            sep = "=" if "=" in line else ":"
            key, _, value = line.partition(sep)
            facts.append(
                Fact(
                    kind=FactKind.CONFIG_VALUE,
                    file=file,
                    line=lineno,
                    repo_id=repo_id,
                    data={"key": key.strip(), "value": value.strip(), "format": "properties"},
                )
            )
            if len(facts) >= _FACT_LIMIT_PER_FILE:
                break
        return facts

    def _from_env(self, content: str, *, file: str, repo_id: str) -> list[Fact]:
        facts: list[Fact] = []
        for lineno, raw in enumerate(content.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            facts.append(
                Fact(
                    kind=FactKind.CONFIG_VALUE,
                    file=file,
                    line=lineno,
                    repo_id=repo_id,
                    data={"key": key.strip(), "value": value, "format": "env"},
                )
            )
        return facts


def _flatten(data: Any, *, prefix: str):  # type: ignore[no-untyped-def]
    """Yield (dotted_key, leaf_value) pairs. Lists are skipped intentionally —
    we don't have a great way to address list elements in route resolution,
    and most relevant config keys are scalars."""
    if isinstance(data, dict):
        for key, value in data.items():
            sub = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                yield from _flatten(value, prefix=sub)
            elif isinstance(value, list):
                continue
            else:
                yield sub, value
    elif prefix:
        yield prefix, data


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
