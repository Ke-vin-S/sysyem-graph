"""Tests for the LLM prompt builders.

Pure-function — no network, no SDK. Asserts the rendered text contains
the structural pieces downstream tooling depends on (so an accidental
edit that drops the schema block fails loudly)."""

from __future__ import annotations

from core.llm.prompts import (
    MAX_CONTENT_CHARS,
    SYSTEM_PROMPT,
    build_extract_facts_prompt,
    build_learn_profile_prompt,
    build_resolve_subgraph_prompt,
    language_for,
)


def test_language_for_known_and_unknown_extensions() -> None:
    assert language_for(".go") == "Go"
    assert language_for(".RB") == "Ruby"  # case-insensitive
    assert language_for(".unknown_ext") == "unknown_ext"
    assert language_for("") == "unknown"


def test_extract_facts_prompt_carries_language_hint_and_line_numbers() -> None:
    prompt = build_extract_facts_prompt(
        file_path="src/main.go",
        content="package main\nfunc main() {}\n",
        extension=".go",
    )
    assert prompt.system == SYSTEM_PROMPT
    assert prompt.language == "Go"
    assert "written in Go" in prompt.user
    # Line numbers must be prefixed so the model can return positions.
    assert "1: package main" in prompt.user
    assert "2: func main() {}" in prompt.user
    # Schema must be present — downstream tooling parses it from here.
    assert '"kind":' in prompt.user
    assert '"facts":' in prompt.user


def test_extract_facts_prompt_truncates_oversize_content() -> None:
    blob = "x" * (MAX_CONTENT_CHARS + 2_000)
    prompt = build_extract_facts_prompt(
        file_path="huge.go", content=blob, extension=".go"
    )
    # Should contain the truncation marker, not the full content.
    assert "truncated" in prompt.user
    # The full untruncated run of x's is NOT in the prompt.
    assert ("x" * (MAX_CONTENT_CHARS + 1)) not in prompt.user
    # Sanity: shorter than the schema doc + the full blob would be.
    assert len(prompt.user) < len(blob) + 10_000


def test_learn_profile_prompt_includes_repo_and_samples() -> None:
    prompt = build_learn_profile_prompt(
        repo_id="billing",
        samples=[
            ("src/charge.py", "def charge(): pass\n", "representative source"),
            ("pyproject.toml", "[tool.pytest.ini_options]\n", "configuration"),
        ],
    )
    assert "Repo: billing" in prompt.user
    assert "src/charge.py" in prompt.user
    assert "representative source" in prompt.user
    assert "pyproject.toml" in prompt.user


def test_resolve_subgraph_prompt_serialises_facts_and_schema() -> None:
    prompt = build_resolve_subgraph_prompt(
        question="Which handler is bound to /orders?",
        expected_schema={"target_id": "string|null", "confidence": "number"},
        facts=[{"kind": "decorator", "data": {"callee": "router.get"}}],
        snippets={"src/handlers.py": "@router.get('/orders')\ndef list_orders(): ..."},
    )
    assert "Which handler" in prompt.user
    assert '"target_id"' in prompt.user
    assert "router.get" in prompt.user
    assert "src/handlers.py" in prompt.user


def test_extract_facts_prompt_handles_empty_content() -> None:
    """Empty file should still produce a syntactically-valid prompt the
    LLM can refuse (returning `{"facts": []}`)."""
    prompt = build_extract_facts_prompt(
        file_path="empty.go", content="", extension=".go"
    )
    # Numbered content block exists but is empty/minimal.
    assert "Respond with the JSON object only." in prompt.user
