"""Env-driven settings for system-graph.

All credentials and tunables come from environment variables (or a `.env` file
loaded automatically by pydantic-settings). Nothing should be hardcoded in
adapter code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Neo4jSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEO4J_", env_file=".env", extra="ignore")

    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: SecretStr = SecretStr("password")
    database: str = "neo4j"


class DatadogSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DD_", env_file=".env", extra="ignore")

    api_key: SecretStr | None = None
    app_key: SecretStr | None = None
    site: str = "datadoghq.com"
    trace_lookback_hours: int = Field(default=720, ge=1)
    env: str = ""
    """Optional env tag to scope the span query (e.g. 'prod'). Empty = no filter."""
    spans_ttl_seconds: int = Field(default=300, ge=0)
    """How long the staged spans table stays fresh between runs."""
    catalog_ttl_seconds: int = Field(default=3600, ge=0)
    """How long the staged service catalog stays fresh."""
    store_path: str = "./out/datadog.db"
    """Where to keep the SQLite staging store."""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.app_key)


class GitHubSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GITHUB_", env_file=".env", extra="ignore")

    token: SecretStr | None = None
    api_url: str = "https://api.github.com"
    repos: Annotated[tuple[str, ...], NoDecode] = ()
    """Comma-separated repo URLs (`https://github.com/owner/name`) or
    short-form `owner/name`. Both are accepted; short-form is normalized to
    a URL by the service layer.

    `NoDecode` annotation suppresses pydantic-settings' default JSON-decode
    pass on tuple fields so our CSV `_split_csv` validator runs first."""

    clones_dir: Path = Path("./out/github_repos")
    """Where shallow clones live. One subdirectory per `owner/name`."""

    store_path: str = "./out/github.db"
    """SQLite metadata DB tracking last-ingested SHA per repo."""

    default_branch: str = ""
    """If set, clone this branch instead of the remote's default HEAD.
    Empty = follow origin/HEAD."""

    @field_validator("repos", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @property
    def enabled(self) -> bool:
        # Public repos are clonable without a token, so we no longer gate on it.
        return True


class OracleStackSettings(BaseSettings):
    """Knobs for the Oracle-stack ingestion (PL/SQL, Pro*C, C, sh, Forms)."""

    model_config = SettingsConfigDict(
        env_prefix="ORACLE_", env_file=".env", extra="ignore"
    )

    forms_apps: Annotated[tuple[str, ...], NoDecode] = ()
    """Names of Oracle Forms apps that don't appear as `.fmb`/`.fmx` files
    in the source tree (e.g. legacy forms checked into a binary registry).
    Each name surfaces as an additional `Service(language='oracle_forms')`
    in the graph. Set via `ORACLE_FORMS_APPS=acme_orders,acme_billing`."""

    @field_validator("forms_apps", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value


class TestParserSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TESTPARSER_", env_file=".env", extra="ignore")

    root: Path = Path("./examples/test_project")
    """Local filesystem path. Default interpretation: if `root` itself looks
    like a repository (contains .git / pyproject.toml / package.json / etc.)
    it IS the single service. Otherwise its subdirectories are treated as
    individual repos. Override with `TESTPARSER_SINGLE_REPO=true|false`."""

    single_repo: bool | None = None
    """Force single-repo mode (`true`) or parent-of-repos mode (`false`).
    `None` (default) = auto-detect from repo markers at the root."""


class Settings(BaseSettings):
    """Top-level settings bundle. Sub-settings are instantiated lazily."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def neo4j(self) -> Neo4jSettings:
        return Neo4jSettings()

    @property
    def datadog(self) -> DatadogSettings:
        return DatadogSettings()

    @property
    def github(self) -> GitHubSettings:
        return GitHubSettings()

    @property
    def testparser(self) -> TestParserSettings:
        return TestParserSettings()

    @property
    def oracle_stack(self) -> OracleStackSettings:
        return OracleStackSettings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
