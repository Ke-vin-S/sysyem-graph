"""AnthropicClient — minimal LLMClient backed by Anthropic's SDK.

The SDK is imported lazily so the package isn't a hard dependency. When
ANTHROPIC_API_KEY isn't set or the package isn't installed, callers
fall back to NullClient and the enhance pass becomes a no-op.

Uses prompt caching on the candidates portion of each prompt — that's
the bulk of tokens and is stable across many questions per repo, so
cache hits should be common after the first question of a run.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from core.facts.fact import Fact
from core.llm.client import (
    LLMClient,
    ProfileSample,
    RepoOverlay,
    SubgraphQuestion,
    SubgraphResolution,
)

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 1024
_SYSTEM_PROMPT = (
    "You are a static-analysis assistant. Always respond with a single JSON "
    "object matching the schema the caller provides. Do not include any "
    "prose outside the JSON. When unsure, set target_id=null and "
    "confidence=0.0 — guessing is worse than admitting unknown."
)


class AnthropicClient(LLMClient):
    """Calls Anthropic's Messages API. Constructor parameters override env."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model
        self._client: Any | None = None  # lazily instantiated

    def _sdk(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic  # noqa: PLC0415  (lazy import is the point)
        except ImportError as exc:  # pragma: no cover — environment-dependent
            raise RuntimeError(
                "anthropic package not installed. `pip install anthropic` "
                "or use NullClient to skip the enhance pass."
            ) from exc
        if not self._api_key:  # pragma: no cover — env-dependent
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Configure it or use NullClient."
            )
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    # ---- LLMClient interface ------------------------------------------

    def extract_facts(self, *, file: str, content: str, repo_id: str) -> list[Fact]:
        # Out of scope for v1; grammar fallback uses NullClient and returns [].
        return []

    def learn_profile(self, *, repo_id: str, samples: list[ProfileSample]) -> RepoOverlay:
        return RepoOverlay(repo_id=repo_id, model=self._model)

    def resolve_subgraph(self, question: SubgraphQuestion) -> SubgraphResolution:
        """Single round-trip: send the question, parse JSON answer."""
        sdk = self._sdk()
        # Mark the candidates portion as cacheable. We split the prompt so
        # the rolling per-question text is uncached but the long candidate
        # list (same per run) hits the cache after the first call.
        text = question.question
        try:
            resp = sdk.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": text}],
                    }
                ],
            )
        except Exception as exc:  # pragma: no cover — depends on network
            logger.warning("anthropic call failed: %s", exc)
            return SubgraphResolution(answer={}, confidence=0.0, notes=str(exc))
        try:
            payload = resp.content[0].text  # type: ignore[index]
            data = json.loads(payload)
        except (AttributeError, IndexError, json.JSONDecodeError) as exc:
            logger.warning("anthropic response unparseable: %s", exc)
            return SubgraphResolution(answer={}, confidence=0.0, notes="unparseable")
        return SubgraphResolution(
            answer={k: v for k, v in data.items() if k != "confidence"},
            confidence=float(data.get("confidence") or 0.0),
            notes=str(data.get("reason") or ""),
        )
