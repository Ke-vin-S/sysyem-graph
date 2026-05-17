"""Grammar abstract base class.

Concrete grammars (`PythonGrammar`, `JavaGrammar`, `ConfigGrammar`,
`LLMGrammar`) all implement the same `extract(file, content, repo_id)`
contract. The walker picks a grammar by matching `Grammar.suffixes` against
the file's extension; the LLM grammar is the catch-all when no native
grammar claims a suffix.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from core.facts import Fact


class Grammar(ABC):
    #: Filename suffixes this grammar claims. Empty tuple = claim nothing
    #: (typical for `LLMGrammar`, which gets routed by the walker).
    suffixes: tuple[str, ...] = ()

    @abstractmethod
    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        """Return the facts derivable from this file. Never raises on
        malformed input — returns [] instead, so one bad file doesn't kill
        the whole walk."""

    def matches(self, file: Path) -> bool:
        # Dotfiles like `.env` have suffix == "" in pathlib; fall through to
        # full-name match so config grammars can claim them.
        if file.suffix and file.suffix in self.suffixes:
            return True
        return file.name in self.suffixes
