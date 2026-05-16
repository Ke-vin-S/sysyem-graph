"""GitHub adapter configuration."""

from __future__ import annotations

from dataclasses import dataclass

from core.config import GitHubSettings


@dataclass
class GitHubAdapterConfig:
    token: str
    api_url: str = "https://api.github.com"
    repos: tuple[str, ...] = ()
    """List of 'owner/repo' to ingest. Empty = error (no scope)."""

    max_file_bytes: int = 256 * 1024
    """Skip files larger than this. Most source files are well under 256 KiB."""

    file_extensions: tuple[str, ...] = (".py", ".go", ".java", ".ts", ".tsx", ".js")

    @classmethod
    def from_settings(cls, settings: GitHubSettings) -> "GitHubAdapterConfig":
        if not settings.enabled:
            raise ValueError("GITHUB_TOKEN is not configured")
        assert settings.token is not None
        return cls(
            token=settings.token.get_secret_value(),
            api_url=settings.api_url,
            repos=settings.repos,
        )
