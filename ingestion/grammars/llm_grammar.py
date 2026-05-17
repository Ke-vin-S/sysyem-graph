"""LLMGrammar — the any-language catch-all.

Implements `Grammar` by delegating to an `LLMClient`. The default impl uses
`NullClient`, so files routed here today produce no facts. When a real
provider lands, this is the path for `.go`/`.kt`/`.rb`/`.ts` files that have
no native grammar yet.

Suffixes are configurable per-instance; the walker passes the LLMGrammar
whichever extensions remain unclaimed.
"""

from __future__ import annotations

from pathlib import Path

from core.facts import Fact
from core.llm import BudgetExceeded, LLMBudget, LLMCache, LLMClient, NullCache, NullClient
from core.llm.cache import cache_key
from ingestion.grammars.grammar import Grammar


class LLMGrammar(Grammar):
    def __init__(
        self,
        *,
        claimed_suffixes: tuple[str, ...] = (),
        client: LLMClient | None = None,
        cache: LLMCache | None = None,
        budget: LLMBudget | None = None,
        prompt_version: str = "extract-facts-v1",
    ) -> None:
        self._client = client or NullClient()
        self._cache = cache or NullCache()
        self._budget = budget or LLMBudget()
        self._prompt_version = prompt_version
        self.suffixes = tuple(claimed_suffixes)

    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        if not content.strip():
            return []
        key = cache_key(prompt_version=self._prompt_version, content=content, extra=file.suffix)
        cached = self._cache.get(key)
        if cached is not None:
            return [Fact.model_validate(item) for item in cached.get("facts", [])]
        try:
            self._budget.check(est_tokens_in=_estimate_tokens(content))
        except BudgetExceeded:
            if self._budget.fail_open:
                return []
            raise
        facts = self._client.extract_facts(file=str(file), content=content, repo_id=repo_id)
        self._budget.record(tokens_in=_estimate_tokens(content), files=1)
        # Drop the computed `id` field so the round-trip through validate()
        # doesn't trip on extra="forbid".
        self._cache.put(
            key,
            {"facts": [f.model_dump(mode="json", exclude={"id"}) for f in facts]},
        )
        return facts


def _estimate_tokens(content: str) -> int:
    """Cheap heuristic: ~4 chars per token. Real providers replace this."""
    return max(1, len(content) // 4)
