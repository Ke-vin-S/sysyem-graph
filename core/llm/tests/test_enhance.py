"""LLMEnhancer tests — NullClient path (no-op) and a stub-client path."""

from __future__ import annotations

from core.adapters.merger import MergedResult
from core.facts import Fact, FactKind, FactTree
from core.llm import LLMEnhancer, NullCache, NullClient
from core.llm.client import LLMClient, SubgraphQuestion, SubgraphResolution
from core.types import CodeArtifact, LineRange


def _fn(name: str, *, file: str, start: int, end: int) -> CodeArtifact:
    return CodeArtifact(
        id=f"fn:r:{file}:{name}", repoId="r", type="function", name=name,
        file=file, lineRange=LineRange(start=start, end=end), isPublic=True,
    )


def _call(file: str, line: int, callee: str) -> Fact:
    receiver, _, method = callee.rpartition(".")
    return Fact(
        kind=FactKind.CALL, file=file, line=line, repo_id="r",
        data={"callee": callee, "receiver": receiver, "method": method, "args": [], "kwargs": {}},
    )


def test_null_client_is_a_clean_noop() -> None:
    """The default enhancer must run without a provider and produce nothing."""
    enhancer = LLMEnhancer.null()
    merged = MergedResult()
    merged.artifacts["fn:r:src/a.py:foo"] = _fn("foo", file="src/a.py", start=1, end=5)
    tree = FactTree.from_facts("r", [_call("src/a.py", 3, "external.thing")])
    stats = enhancer.enhance(merged, tree)
    assert merged.suggestions == {}
    assert stats.proposals_made == 0
    assert "NullClient" in stats.notes[0]


class _StubClient(LLMClient):
    """A LLMClient that always returns a fixed answer for resolve_subgraph."""

    def __init__(self, answer: dict, confidence: float, notes: str = "stub") -> None:
        self._answer = answer
        self._conf = confidence
        self._notes = notes

    def extract_facts(self, **_):
        return []

    def learn_profile(self, **_):
        raise NotImplementedError

    def resolve_subgraph(self, question: SubgraphQuestion) -> SubgraphResolution:
        return SubgraphResolution(answer=self._answer, confidence=self._conf, notes=self._notes)


def test_stub_client_emits_suggestion_when_confidence_high() -> None:
    caller = _fn("foo", file="src/a.py", start=1, end=10)
    target = _fn("helper", file="src/b.py", start=1, end=3)
    merged = MergedResult()
    merged.artifacts[caller.id] = caller
    merged.artifacts[target.id] = target
    tree = FactTree.from_facts("r", [_call("src/a.py", 5, "helper")])

    enhancer = LLMEnhancer(
        client=_StubClient(answer={"target_id": target.id}, confidence=0.9, notes="match"),
        cache=NullCache(),
    )
    stats = enhancer.enhance(merged, tree)
    assert len(merged.suggestions) == 1
    sug = next(iter(merged.suggestions.values()))
    assert sug.src_id == caller.id and sug.dst_id == target.id and sug.rel == "CALLS"
    assert sug.confidence == 0.9
    assert stats.questions_asked == 1


def test_low_confidence_dropped() -> None:
    caller = _fn("foo", file="src/a.py", start=1, end=10)
    target = _fn("helper", file="src/b.py", start=1, end=3)
    merged = MergedResult()
    merged.artifacts[caller.id] = caller
    merged.artifacts[target.id] = target
    tree = FactTree.from_facts("r", [_call("src/a.py", 5, "helper")])

    enhancer = LLMEnhancer(
        client=_StubClient(answer={"target_id": target.id}, confidence=0.3, notes="weak"),
        cache=NullCache(),
        min_confidence=0.6,
    )
    enhancer.enhance(merged, tree)
    assert merged.suggestions == {}


def test_unknown_target_id_dropped() -> None:
    """LLM might hallucinate an id that doesn't exist in the artifact index."""
    caller = _fn("foo", file="src/a.py", start=1, end=10)
    merged = MergedResult()
    merged.artifacts[caller.id] = caller
    tree = FactTree.from_facts("r", [_call("src/a.py", 5, "helper")])

    enhancer = LLMEnhancer(
        client=_StubClient(answer={"target_id": "fn:r:src/ghost.py:nope"}, confidence=0.99),
        cache=NullCache(),
    )
    enhancer.enhance(merged, tree)
    assert merged.suggestions == {}


def test_resolver_bound_calls_arent_re_asked() -> None:
    """If the resolver already bound a call (caller.calls includes the target),
    the enhancer must not ask the LLM about it again."""
    target = _fn("helper", file="src/b.py", start=1, end=3)
    caller = _fn("foo", file="src/a.py", start=1, end=10).model_copy(update={"calls": (target.id,)})
    merged = MergedResult()
    merged.artifacts[caller.id] = caller
    merged.artifacts[target.id] = target
    tree = FactTree.from_facts("r", [_call("src/a.py", 5, "helper")])

    enhancer = LLMEnhancer(
        client=_StubClient(answer={"target_id": target.id}, confidence=0.99),
        cache=NullCache(),
    )
    stats = enhancer.enhance(merged, tree)
    assert stats.questions_asked == 0
    assert merged.suggestions == {}


def test_null_client_works_through_factory() -> None:
    """LLMEnhancer.null() short-circuits even with facts present."""
    enhancer = LLMEnhancer.null()
    merged = MergedResult()
    tree = FactTree.from_facts("r", [_call("src/x.py", 1, "y")])
    stats = enhancer.enhance(merged, tree)
    assert isinstance(enhancer.client, NullClient)
    assert stats.proposals_made == 0
