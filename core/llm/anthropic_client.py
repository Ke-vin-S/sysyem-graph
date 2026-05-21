"""AnthropicClient — minimal LLMClient backed by Anthropic's SDK.

The SDK is imported lazily so the package isn't a hard dependency. When
ANTHROPIC_API_KEY isn't set or the package isn't installed, callers
fall back to NullClient and the enhance pass becomes a no-op.

Uses prompt caching on the system prompt (small, stable) so we don't
pay re-tokenisation cost on every call within a run.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from core.facts.fact import Fact
from core.llm.client import (
    LLMClient,
    ProfileSample,
    RepoOverlay,
    SubgraphQuestion,
    SubgraphResolution,
)
from core.llm.parser import parse_extract_facts_response
from core.llm.prompts import (
    build_extract_facts_prompt,
    build_learn_profile_prompt,
    build_resolve_subgraph_prompt,
)

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5"
_MAX_TOKENS_EXTRACT = 4096
_MAX_TOKENS_PROFILE = 2048
_MAX_TOKENS_SUBGRAPH = 1024


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
        """Ask the LLM to extract structured Facts from `content`.

        Called by `LLMGrammar` when the walker can't find a native
        grammar for a file's extension. The model is given the file
        line-by-line and asked to return a JSON list of records, which
        the parser validates and converts to Fact instances. Failures
        return [] rather than raise — better to lose a file than to
        crash the whole run.
        """
        extension = PurePosixPath(file).suffix
        prompt = build_extract_facts_prompt(
            file_path=file, content=content, extension=extension
        )
        payload = self._call(
            system=prompt.system,
            user=prompt.user,
            max_tokens=_MAX_TOKENS_EXTRACT,
        )
        if payload is None:
            return []
        return parse_extract_facts_response(payload, file=file, repo_id=repo_id)

    def learn_profile(self, *, repo_id: str, samples: list[ProfileSample]) -> RepoOverlay:
        prompt = build_learn_profile_prompt(
            repo_id=repo_id,
            samples=[(s.path, s.content, s.notes) for s in samples],
        )
        payload = self._call(
            system=prompt.system,
            user=prompt.user,
            max_tokens=_MAX_TOKENS_PROFILE,
        )
        if payload is None:
            return RepoOverlay(
                repo_id=repo_id,
                model=self._model,
                generated_at=datetime.now(timezone.utc),
                notes="anthropic call failed",
            )
        data = _safe_json(payload) or {}
        return RepoOverlay(
            repo_id=repo_id,
            test_annotations=tuple(_as_str_list(data.get("test_annotations"))),
            mock_annotations=tuple(_as_str_list(data.get("mock_annotations"))),
            external_modules=tuple(_as_str_list(data.get("external_modules"))),
            internal_test_wrappers=tuple(_as_str_list(data.get("internal_test_wrappers"))),
            notes=str(data.get("notes") or ""),
            generated_at=datetime.now(timezone.utc),
            model=self._model,
        )

    def resolve_subgraph(self, question: SubgraphQuestion) -> SubgraphResolution:
        """Single round-trip: send the question, parse JSON answer."""
        prompt = build_resolve_subgraph_prompt(
            question=question.question,
            expected_schema=question.expected_schema,
            facts=[f.model_dump(mode="json") for f in question.facts],
            snippets=question.file_snippets,
        )
        payload = self._call(
            system=prompt.system,
            user=prompt.user,
            max_tokens=_MAX_TOKENS_SUBGRAPH,
        )
        if payload is None:
            return SubgraphResolution(answer={}, confidence=0.0, notes="anthropic call failed")
        data = _safe_json(payload) or {}
        return SubgraphResolution(
            answer={k: v for k, v in data.items() if k not in {"confidence", "reason"}},
            confidence=float(data.get("confidence") or 0.0),
            notes=str(data.get("reason") or ""),
        )

    # ---- internals -----------------------------------------------------

    def _call(self, *, system: str, user: str, max_tokens: int) -> str | None:
        """One call to the Messages API. Returns the text content of the
        first content block, or None on failure (caller decides how to
        recover)."""
        try:
            sdk = self._sdk()
        except RuntimeError as exc:  # pragma: no cover — env-dependent
            logger.warning("anthropic unavailable: %s", exc)
            return None
        try:
            resp = sdk.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": [{"type": "text", "text": user}]}],
            )
        except Exception as exc:  # pragma: no cover — network-dependent
            logger.warning("anthropic call failed: %s", exc)
            return None
        try:
            return resp.content[0].text  # type: ignore[index]
        except (AttributeError, IndexError) as exc:
            logger.warning("anthropic response missing text block: %s", exc)
            return None


def _safe_json(payload: str) -> dict[str, Any] | None:
    """Parse a JSON object payload tolerantly. Returns None on failure.

    Mirrors the parser's behaviour: strip a fenced block if present, then
    json.loads."""
    text = payload.strip()
    if text.startswith("```"):
        # Drop the first line ('```json' or '```') and a trailing fence.
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("anthropic response unparseable: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]
