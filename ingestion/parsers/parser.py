"""Parser interface for language-specific test extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedTest:
    """Result of parsing one test function/method from a source file."""

    name: str
    file: str
    line_start: int
    line_end: int
    decorators: tuple[str, ...] = field(default_factory=tuple)
    imports: tuple[str, ...] = field(default_factory=tuple)
    """Modules imported by the file containing this test. Used by the classifier
    to distinguish a unit test (no I/O imports) from an integration test
    (`requests`, `httpx`, DB clients, etc.)."""

    mocked_modules: tuple[str, ...] = field(default_factory=tuple)
    """Modules that the file explicitly patches/mocks. A test that mocks
    everything is structurally a unit test even if it imports `httpx`."""

    calls_external: bool = False
    """Heuristic: contains real HTTP/DB calls that aren't behind a mock."""


class Parser(ABC):
    """A language-specific parser. One implementation per language."""

    #: Filename suffixes this parser claims (e.g. ``(".py",)``).
    suffixes: tuple[str, ...] = ()

    @abstractmethod
    def parse(self, file: Path, content: str) -> list[ParsedTest]:
        """Return one ParsedTest per test function found in `content`."""

    def matches(self, file: Path) -> bool:
        return file.suffix in self.suffixes
