"""Test parser configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.config import TestParserSettings


@dataclass
class TestParserAdapterConfig:
    root: Path
    """Filesystem path. Treated either as a single repository (when it
    contains repo markers like `.git`, `pyproject.toml`, `package.json`,
    or when `single_repo=True`) or as a parent directory whose
    subdirectories are individual repos (the legacy default)."""

    single_repo: bool | None = None
    """`True` → root IS the service. `False` → walk subdirectories.
    `None` → auto-detect from repo markers at the root."""

    repo_id_strategy: str = "dirname"
    """How to derive a repo_id for tests. 'dirname' = top-level subdirectory name."""

    test_path_patterns: tuple[str, ...] = (
        "test_*.py",
        "*_test.py",
        "tests/**/*.py",
        "**/tests/**/*.py",
    )
    excluded_dirs: tuple[str, ...] = field(
        default_factory=lambda: (
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            "build",
            "dist",
        )
    )

    @classmethod
    def from_settings(cls, settings: TestParserSettings) -> "TestParserAdapterConfig":
        return cls(
            root=settings.root.resolve(),
            single_repo=settings.single_repo,
        )
