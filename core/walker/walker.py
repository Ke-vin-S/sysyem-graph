"""Walker: walks a repo's filesystem and dispatches each file to a Grammar.

The walker is dumb about meaning — it picks files by suffix, calls the
matching grammar, accumulates facts. Resolvers downstream do the
interpretation. This split is the whole point of Phase 1.5: framework
knowledge lives in YAML, fact extraction lives in grammars, meaning lives
in resolvers.

Routing rule:
  1. If any native Grammar's `suffixes` claim the file, use the first match
     (grammar list order = priority).
  2. Otherwise, if an `llm_grammar` is provided and its `suffixes` claim it,
     use that.
  3. Otherwise, skip the file.

This keeps the LLM out of the critical path for languages we already
support natively, while still letting it cover new-language files.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from core.facts import Fact, FactTree
from ingestion.grammars import (
    ConfigGrammar,
    Grammar,
    JavaGrammar,
    LLMGrammar,
    PythonGrammar,
)

logger = logging.getLogger(__name__)


@dataclass
class WalkerConfig:
    excluded_dirs: tuple[str, ...] = (
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "build",
        "dist",
        "target",
        "out",
        ".system-graph",
    )
    max_file_bytes: int = 1_000_000
    """Skip files larger than this — defends against committed binaries."""

    follow_symlinks: bool = False


@dataclass
class Walker:
    """File-tree walker that emits a FactTree per repo.

    Pass `grammars` to override the default set. The default includes Python,
    Java, and Config grammars; the LLM grammar is opt-in via `llm_grammar`
    because most callers don't want a 3rd-party call by default.
    """

    grammars: list[Grammar] = field(
        default_factory=lambda: [PythonGrammar(), JavaGrammar(), ConfigGrammar()]
    )
    llm_grammar: LLMGrammar | None = None
    config: WalkerConfig = field(default_factory=WalkerConfig)

    def walk(self, root: Path, *, repo_id: str) -> FactTree:
        """Walk `root`, dispatch each file to a grammar, return a FactTree."""
        tree = FactTree(repo_id=repo_id)
        if not root.exists():
            return tree
        if root.is_file():
            grammar = self._grammar_for(root)
            if grammar is not None:
                tree.extend(self._extract(root, grammar, repo_id))
            return tree

        for file in self._iter_files(root):
            grammar = self._grammar_for(file)
            if grammar is None:
                continue
            tree.extend(self._extract(file, grammar, repo_id))
        return tree

    def _grammar_for(self, file: Path) -> Grammar | None:
        for grammar in self.grammars:
            if grammar.matches(file):
                return grammar
        if self.llm_grammar is not None and self.llm_grammar.matches(file):
            return self.llm_grammar
        return None

    def _extract(self, file: Path, grammar: Grammar, repo_id: str) -> Iterable[Fact]:
        try:
            size = file.stat().st_size
        except OSError as exc:
            logger.warning("walker: stat failed for %s: %s", file, exc)
            return []
        if size > self.config.max_file_bytes:
            return []
        try:
            content = file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("walker: read failed for %s: %s", file, exc)
            return []
        return grammar.extract(file, content, repo_id=repo_id)

    def _iter_files(self, root: Path) -> Iterator[Path]:
        excluded = set(self.config.excluded_dirs)
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in excluded for part in path.parts):
                continue
            if not self.config.follow_symlinks and path.is_symlink():
                continue
            yield path
