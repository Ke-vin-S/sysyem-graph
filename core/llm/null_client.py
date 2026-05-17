"""NullClient — the default LLMClient implementation.

Returns empty/no-op for every method. Lets the system run today exactly as
it did before the LLM layer existed: deterministic grammars do all the work,
the LLM slot is wired but inert.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.facts.fact import Fact
from core.llm.client import (
    LLMClient,
    ProfileSample,
    RepoOverlay,
    SubgraphQuestion,
    SubgraphResolution,
)


class NullClient(LLMClient):
    """No-op client. All three methods return their minimal valid response."""

    def extract_facts(self, *, file: str, content: str, repo_id: str) -> list[Fact]:
        return []

    def learn_profile(self, *, repo_id: str, samples: list[ProfileSample]) -> RepoOverlay:
        return RepoOverlay(
            repo_id=repo_id,
            notes="null client; no LLM consulted",
            generated_at=datetime.now(timezone.utc),
            model="null",
        )

    def resolve_subgraph(self, question: SubgraphQuestion) -> SubgraphResolution:
        return SubgraphResolution(answer={}, confidence=0.0, notes="null client")
