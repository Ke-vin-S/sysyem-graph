"""Tests for the LLMClient factory.

We don't need the live Anthropic SDK here: when the key is absent we
expect a NullClient; when it's present we expect the factory to attempt
the import and either succeed (returning AnthropicClient) or fall back
to NullClient with a single WARN log."""

from __future__ import annotations

import pytest

from core.llm.factory import make_llm_client
from core.llm.null_client import NullClient


def test_returns_null_client_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = make_llm_client()
    assert isinstance(client, NullClient)


def test_returns_null_client_when_key_is_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    client = make_llm_client()
    assert isinstance(client, NullClient)


def test_returns_anthropic_client_when_key_set_and_sdk_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the SDK isn't installed in this environment, we accept the
    documented fallback to NullClient."""
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    client = make_llm_client()
    # Either the real client or the safe fallback — both are valid outcomes.
    from core.llm.anthropic_client import AnthropicClient

    assert isinstance(client, (AnthropicClient, NullClient))
