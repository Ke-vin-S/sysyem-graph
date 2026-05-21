"""Tests for AnthropicClient.extract_facts using a mocked SDK.

We monkey-patch the SDK boundary so we never make a network call. The
goal is to confirm the wiring: prompt is built, the SDK is called with
system + user, the text payload is fed into the parser, and `Fact`s
come out the other side.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.llm.anthropic_client import AnthropicClient


class _FakeMessagesAPI:
    """Drop-in for sdk.messages with a configurable response."""

    def __init__(self, text_payload: str) -> None:
        self._payload = text_payload
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):  # noqa: ANN003 — mimicking the SDK
        self.last_kwargs = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=self._payload)])


class _FakeSDK:
    def __init__(self, payload: str) -> None:
        self.messages = _FakeMessagesAPI(payload)


@pytest.fixture()
def fake_response_facts() -> str:
    return json.dumps(
        {
            "facts": [
                {
                    "kind": "symbol",
                    "line": 1,
                    "data": {
                        "sym_kind": "function",
                        "name": "doit",
                        "enclosing_class": "",
                        "enclosing_package": "",
                    },
                }
            ]
        }
    )


def test_extract_facts_wires_prompt_and_parses_response(
    fake_response_facts: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = AnthropicClient(api_key="sk-test", model="claude-test-model")
    fake_sdk = _FakeSDK(fake_response_facts)
    monkeypatch.setattr(client, "_sdk", lambda: fake_sdk)

    facts = client.extract_facts(
        file="src/main.go",
        content="package main\nfunc doit() {}\n",
        repo_id="myrepo",
    )

    assert len(facts) == 1
    assert facts[0].file == "src/main.go"
    assert facts[0].repo_id == "myrepo"
    assert facts[0].kind.value == "symbol"

    # Verify the SDK got the expected shape.
    kwargs = fake_sdk.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "claude-test-model"
    # System prompt is cached; user content is the actual prompt body.
    assert kwargs["system"][0]["cache_control"]["type"] == "ephemeral"
    user_text = kwargs["messages"][0]["content"][0]["text"]
    assert "written in Go" in user_text
    assert "src/main.go" in user_text


def test_extract_facts_returns_empty_when_sdk_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If _sdk() raises (key missing, package missing), extract_facts
    must NOT crash the walker — return []."""
    client = AnthropicClient(api_key="")
    def _raise() -> None:
        raise RuntimeError("no key")

    monkeypatch.setattr(client, "_sdk", _raise)
    out = client.extract_facts(file="x.go", content="x", repo_id="r")
    assert out == []


def test_extract_facts_returns_empty_on_unparseable_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AnthropicClient(api_key="sk-test")
    monkeypatch.setattr(client, "_sdk", lambda: _FakeSDK("definitely not json"))
    out = client.extract_facts(file="x.go", content="x", repo_id="r")
    assert out == []
