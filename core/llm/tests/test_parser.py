"""Tests for the LLM response parser.

The parser is intentionally tolerant of small formatting drift (fenced
JSON blocks, extra whitespace) but strict about contract violations
(unknown kinds, missing required fields). These tests pin both behaviours.
"""

from __future__ import annotations

import json

from core.llm.parser import parse_extract_facts_response


def test_well_formed_response_yields_facts() -> None:
    payload = json.dumps(
        {
            "facts": [
                {
                    "kind": "symbol",
                    "line": 5,
                    "line_end": 9,
                    "data": {
                        "sym_kind": "function",
                        "name": "charge",
                        "enclosing_class": "",
                        "enclosing_package": "billing",
                    },
                },
                {
                    "kind": "call",
                    "line": 7,
                    "data": {
                        "callee": "pkg_audit.log",
                        "receiver": "pkg_audit",
                        "method": "log",
                        "args": [],
                        "kwargs": {},
                    },
                },
            ]
        }
    )
    facts = parse_extract_facts_response(payload, file="src/charge.go", repo_id="billing")
    assert len(facts) == 2
    assert facts[0].kind.value == "symbol"
    assert facts[0].file == "src/charge.go"
    assert facts[0].repo_id == "billing"
    assert facts[0].line == 5
    assert facts[0].line_end == 9
    assert facts[1].kind.value == "call"


def test_fenced_json_block_is_unwrapped() -> None:
    """Some models stubbornly wrap their JSON in ```json fences despite
    the system prompt. The parser strips them rather than failing."""
    payload = '```json\n{"facts": [{"kind":"import","line":1,"data":{"module":"fmt"}}]}\n```'
    facts = parse_extract_facts_response(payload, file="x.go", repo_id="r")
    assert len(facts) == 1
    assert facts[0].kind.value == "import"


def test_unknown_kind_is_silently_skipped() -> None:
    """The prompt only documents a fixed kind set; anything else is a
    hallucination and gets dropped without halting other records."""
    payload = json.dumps(
        {
            "facts": [
                {"kind": "made_up_kind", "line": 1, "data": {}},
                {"kind": "symbol", "line": 2, "data": {"sym_kind": "function", "name": "f"}},
            ]
        }
    )
    facts = parse_extract_facts_response(payload, file="x.go", repo_id="r")
    assert len(facts) == 1
    assert facts[0].kind.value == "symbol"


def test_missing_line_skips_only_that_record() -> None:
    payload = json.dumps(
        {
            "facts": [
                {"kind": "symbol", "data": {"name": "no_line"}},
                {"kind": "symbol", "line": 3, "data": {"name": "ok"}},
            ]
        }
    )
    facts = parse_extract_facts_response(payload, file="x.go", repo_id="r")
    assert len(facts) == 1
    assert facts[0].data["name"] == "ok"


def test_bad_line_end_is_dropped_not_fatal() -> None:
    """`line_end < line` is a soft error — the field is cleared but the
    rest of the record survives. Same for non-int values."""
    payload = json.dumps(
        {
            "facts": [
                {"kind": "symbol", "line": 5, "line_end": 2, "data": {"name": "a"}},
                {"kind": "symbol", "line": 5, "line_end": "huh", "data": {"name": "b"}},
            ]
        }
    )
    facts = parse_extract_facts_response(payload, file="x.go", repo_id="r")
    assert len(facts) == 2
    assert all(f.line_end is None for f in facts)


def test_non_json_payload_returns_empty() -> None:
    facts = parse_extract_facts_response(
        "Sorry, I cannot do that.", file="x.go", repo_id="r"
    )
    assert facts == []


def test_payload_without_facts_key_returns_empty() -> None:
    payload = json.dumps({"items": [{"kind": "symbol", "line": 1, "data": {}}]})
    facts = parse_extract_facts_response(payload, file="x.go", repo_id="r")
    assert facts == []


def test_empty_facts_list_is_valid() -> None:
    facts = parse_extract_facts_response('{"facts": []}', file="x.go", repo_id="r")
    assert facts == []


def test_file_and_repo_id_attached_to_every_fact() -> None:
    payload = json.dumps(
        {
            "facts": [
                {"kind": "symbol", "line": 1, "data": {"name": "a"}},
                {"kind": "symbol", "line": 2, "data": {"name": "b"}},
            ]
        }
    )
    facts = parse_extract_facts_response(
        payload, file="src/legacy.cob", repo_id="mainframe"
    )
    assert all(f.file == "src/legacy.cob" for f in facts)
    assert all(f.repo_id == "mainframe" for f in facts)
