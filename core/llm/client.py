"""LLMClient interface and the structured types it exchanges.

The interface is narrow on purpose: three methods, each with a strict
input/output schema. Providers are free to implement the same call however
their SDK wants (chat completion, structured output, tool use), but the
boundary is JSON-validated dataclasses both ways.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.facts.fact import Fact


@dataclass
class LLMResponse:
    text: str
    json: dict[str, Any] | None
    tokens_in: int
    tokens_out: int
    model: str
    request_id: str = ""
    """Provider request ID for audit/replay."""


@dataclass
class ProfileSample:
    """One file's worth of input to the profile-learning prompt.

    We pick a handful of these per repo (representative source files +
    config files) so the LLM can identify the frameworks and any in-house
    conventions without us shipping the entire repo.
    """

    path: str
    content: str
    """Truncated to a budget the caller decides."""

    notes: str = ""
    """Optional hint, e.g. 'configuration file' or 'representative test'."""


@dataclass
class RepoOverlay:
    """LLM-learned extension to the stock framework definitions for one repo.

    Same schema as FrameworkDefinition but ADDITIVE — overlay never replaces
    a stock value, only adds to it. The loader merges (overlay ∪ stock) at
    runtime, so a repo that wraps `httpx` as `acme.http.client` ends up with
    both names in the effective `external_modules` list.
    """

    repo_id: str
    test_annotations: tuple[str, ...] = ()
    mock_annotations: tuple[str, ...] = ()
    external_modules: tuple[str, ...] = ()
    internal_test_wrappers: tuple[str, ...] = ()
    notes: str = ""
    generated_at: datetime | None = None
    model: str = ""
    overlay_version: str = "1"


@dataclass
class SubgraphQuestion:
    """A bundled question for the LLM when a resolver gets stuck.

    Used when, for example, the route prefix depends on a runtime branch the
    resolver can't statically evaluate. We give the LLM the relevant facts
    plus a structured question and ask for a specific answer shape.
    """

    question: str
    facts: list[Fact] = field(default_factory=list)
    file_snippets: dict[str, str] = field(default_factory=dict)
    """Path -> relevant snippet (resolver decides what's relevant)."""

    expected_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubgraphResolution:
    answer: dict[str, Any]
    confidence: float
    notes: str = ""


class LLMClient(ABC):
    """Provider-agnostic LLM interface used by grammars, resolvers, and profile learners."""

    @abstractmethod
    def extract_facts(self, *, file: str, content: str, repo_id: str) -> list[Fact]:
        """Return a list of Facts extracted from `content`.

        Used as a fallback for languages without a native Grammar. The provider
        is expected to validate output against the Fact schema; malformed
        responses should return [] rather than raise.
        """

    @abstractmethod
    def learn_profile(self, *, repo_id: str, samples: list[ProfileSample]) -> RepoOverlay:
        """One-shot per repo: examine sample files, return a RepoOverlay
        that extends the stock framework knowledge for in-house conventions."""

    @abstractmethod
    def resolve_subgraph(self, question: SubgraphQuestion) -> SubgraphResolution:
        """Answer a structured question about a fact subgraph."""
