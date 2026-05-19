"""Env-driven settings for system-graph.

All credentials and tunables come from environment variables (or a `.env` file
loaded automatically by pydantic-settings). Nothing should be hardcoded in
adapter code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    store_path: str = "./out/datadog.db"
    """Where to keep the SQLite staging store."""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.app_key)


class GitHubSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GITHUB_", env_file=".env", extra="ignore")

    token: SecretStr | None = None
    api_url: str = "https://api.github.com"
    repos: tuple[str, ...] = ()
    """Comma-separated 'owner/repo' values are split into a tuple."""

    @field_validator("repos", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @property
    def enabled(self) -> bool:
        return self.token is not None


class TestParserSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TESTPARSER_", env_file=".env", extra="ignore")

    root: Path = Path("./examples/test_project")
    """Local filesystem path containing checked-out repos to scan."""


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
