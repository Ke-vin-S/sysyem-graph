"""Pick an `LLMClient` implementation based on environment.

Centralised so the walker, the enhance pass, and the report tooling all
agree on which provider to hit. The contract:

  * `ANTHROPIC_API_KEY` set + `anthropic` package importable → AnthropicClient
  * anything else                                            → NullClient

The decision is made at *call time*, not import time, so tests can flip
env vars between runs without re-importing modules.
"""

from __future__ import annotations

import logging
import os

from core.llm.client import LLMClient
from core.llm.null_client import NullClient

logger = logging.getLogger(__name__)


def make_llm_client(*, model: str | None = None) -> LLMClient:
    """Return a live LLMClient if credentials are present, else a NullClient.

    Keep the failure path quiet: a missing key isn't a problem for users
    running with native grammars only. Misconfiguration (key set but
    package missing) gets a single WARN line so it's discoverable
    without spamming."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.debug("llm: ANTHROPIC_API_KEY not set, using NullClient")
        return NullClient()
    try:
        from core.llm.anthropic_client import AnthropicClient  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover — env-dependent
        logger.warning(
            "llm: ANTHROPIC_API_KEY set but core.llm.anthropic_client unavailable (%s); "
            "falling back to NullClient",
            exc,
        )
        return NullClient()
    try:
        import anthropic  # noqa: F401, PLC0415
    except ImportError:  # pragma: no cover — depends on user pip env
        logger.warning(
            "llm: ANTHROPIC_API_KEY set but `anthropic` package not installed; "
            "run `pip install anthropic` or unset the key. Using NullClient."
        )
        return NullClient()
    effective_model = model or os.environ.get("ANTHROPIC_MODEL", "").strip()
    if effective_model:
        return AnthropicClient(api_key=api_key, model=effective_model)
    return AnthropicClient(api_key=api_key)


__all__ = ["make_llm_client"]
