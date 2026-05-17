"""LLMGrammar tests with a stub LLM client."""

from __future__ import annotations

from pathlib import Path

from core.facts import Fact, FactKind
from core.llm import LLMBudget, LLMClient, NullCache, ProfileSample, RepoOverlay, SubgraphQuestion, SubgraphResolution
from ingestion.grammars import LLMGrammar


class _StubClient(LLMClient):
    def __init__(self, facts: list[Fact]) -> None:
        self._facts = facts
        self.calls = 0

    def extract_facts(self, *, file: str, content: str, repo_id: str) -> list[Fact]:
        self.calls += 1
        return list(self._facts)

    def learn_profile(self, *, repo_id, samples):
        return RepoOverlay(repo_id=repo_id)

    def resolve_subgraph(self, question: SubgraphQuestion) -> SubgraphResolution:
        return SubgraphResolution(answer={}, confidence=0.0)


def test_default_grammar_returns_empty() -> None:
    grammar = LLMGrammar()
    assert grammar.extract(Path("foo.go"), "package main", repo_id="r") == []


def test_grammar_with_stub_calls_client() -> None:
    fact = Fact(
        kind=FactKind.SYMBOL,
        file="foo.go",
        line=10,
        repo_id="r",
        data={"sym_kind": "function", "name": "Foo"},
    )
    client = _StubClient([fact])
    grammar = LLMGrammar(client=client, claimed_suffixes=(".go",))
    out = grammar.extract(Path("foo.go"), "package main\nfunc Foo() {}", repo_id="r")
    assert client.calls == 1
    assert len(out) == 1
    assert out[0].data["name"] == "Foo"


def test_grammar_budget_fail_open_returns_empty() -> None:
    client = _StubClient([])
    budget = LLMBudget(max_files_per_run=0)  # immediately exceeded
    grammar = LLMGrammar(client=client, budget=budget, claimed_suffixes=(".go",))
    assert grammar.extract(Path("foo.go"), "package main", repo_id="r") == []
    assert client.calls == 0  # short-circuited


def test_grammar_empty_content_returns_empty() -> None:
    client = _StubClient([])
    grammar = LLMGrammar(client=client, claimed_suffixes=(".go",))
    assert grammar.extract(Path("foo.go"), "   \n", repo_id="r") == []
    assert client.calls == 0


def test_grammar_cache_hits_skip_client() -> None:
    from core.llm import FileCache  # noqa: F401  (not used; just keep imports stable)

    class _Memory:
        def __init__(self):
            self.store = {}

        def get(self, key):
            return self.store.get(key)

        def put(self, key, value):
            self.store[key] = value

    cache = _Memory()
    client = _StubClient(
        [Fact(kind=FactKind.SYMBOL, file="x.go", line=1, repo_id="r", data={"sym_kind": "function", "name": "X"})]
    )
    grammar = LLMGrammar(client=client, cache=cache, claimed_suffixes=(".go",))
    out1 = grammar.extract(Path("x.go"), "func X() {}", repo_id="r")
    out2 = grammar.extract(Path("x.go"), "func X() {}", repo_id="r")
    assert client.calls == 1  # second call hit cache
    assert len(out1) == 1 and len(out2) == 1
    assert out1[0].id == out2[0].id
