"""End-to-end: the walker routes unknown-language files to the LLMGrammar.

We mock the LLMClient so no network call happens. The point is to prove
that a `.go` file (no native grammar) hits the LLM path and the returned
facts land in the FactTree."""

from __future__ import annotations

from pathlib import Path

from core.facts.fact import Fact, FactKind
from core.languages import load_library
from core.languages.grammar_registry import build_grammars
from core.languages.library import DEFAULT_LANGUAGES_DIR
from core.llm.client import (
    LLMClient,
    ProfileSample,
    RepoOverlay,
    SubgraphQuestion,
    SubgraphResolution,
)
from core.walker import Walker
from ingestion.grammars import LLMGrammar


class _StubClient(LLMClient):
    """Fake LLM that returns a single SYMBOL Fact per call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def extract_facts(self, *, file: str, content: str, repo_id: str) -> list[Fact]:
        self.calls.append((file, repo_id))
        return [
            Fact(
                kind=FactKind.SYMBOL,
                file=file,
                line=1,
                repo_id=repo_id,
                data={
                    "sym_kind": "function",
                    "name": "main",
                    "enclosing_class": "",
                    "enclosing_package": "main",
                },
            )
        ]

    def learn_profile(self, *, repo_id: str, samples: list[ProfileSample]) -> RepoOverlay:
        return RepoOverlay(repo_id=repo_id)

    def resolve_subgraph(self, question: SubgraphQuestion) -> SubgraphResolution:
        return SubgraphResolution(answer={}, confidence=0.0)


def test_go_file_routes_to_llm_grammar(tmp_path: Path) -> None:
    # A Python file (native) and a Go file (no native grammar; falls
    # through to LLMGrammar).
    (tmp_path / "main.py").write_text("def f(): pass\n")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")

    grammars = build_grammars(load_library(DEFAULT_LANGUAGES_DIR))
    # Swap in the stub client so we don't hit the network.
    stub = _StubClient()
    for g in grammars:
        if isinstance(g, LLMGrammar):
            # Replace the protected client field — this is the wiring
            # seam the factory normally fills.
            g._client = stub  # noqa: SLF001 — test seam
    walker = Walker(grammars=grammars)
    tree = walker.walk(tmp_path, repo_id="r")

    # The stub was called exactly once — for main.go, not main.py.
    assert len(stub.calls) == 1
    called_file, called_repo = stub.calls[0]
    assert called_file.endswith("main.go")
    assert called_repo == "r"

    # And its returned fact landed in the tree.
    symbols = [f for f in tree.where(kind=FactKind.SYMBOL) if f.file.endswith("main.go")]
    assert len(symbols) == 1
    assert symbols[0].data["name"] == "main"
