"""Parse LLM responses into `Fact` records.

Separated from the client so the same parser can validate responses
from any provider AND so it's testable without an SDK present.

Validation philosophy: drop records that don't fit, log them at WARN,
return the rest. A noisy LLM doesn't poison the graph — invalid
records are forfeited, not absorbed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from core.facts.fact import Fact, FactKind

logger = logging.getLogger(__name__)


# Allowed kinds for LLM-extracted facts. Anything outside this set is
# dropped — the prompt only documents these, so values outside the list
# are hallucinations.
_ALLOWED_KINDS = frozenset(
    {
        FactKind.SYMBOL.value,
        FactKind.CLASS_DEF.value,
        FactKind.IMPORT.value,
        FactKind.CALL.value,
        FactKind.DECORATOR.value,
        FactKind.ANNOTATION.value,
        FactKind.STRING_LITERAL.value,
    }
)

# Strip a fenced code block if the model ignored instructions and wrapped
# its JSON in ```json ... ``` despite being told not to. The fence is
# the only common formatting drift we've seen — anything more exotic
# gets caught by json.loads and dropped wholesale.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_extract_facts_response(
    payload: str,
    *,
    file: str,
    repo_id: str,
) -> list[Fact]:
    """Validate an `extract-facts` response. Returns the surviving
    `Fact` records; logs and drops invalid ones.

    `payload` is the raw text the model returned (single content block).
    We accept JSON optionally wrapped in a fenced block. Anything else
    is treated as a parse failure → empty list.
    """
    text = payload.strip()
    if not text:
        return []
    text = _FENCE_RE.sub("", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("llm: response not valid JSON (%s); dropping all facts", exc)
        return []
    if not isinstance(data, dict):
        logger.warning("llm: response JSON is not an object; dropping")
        return []
    records = data.get("facts")
    if not isinstance(records, list):
        logger.warning("llm: response missing `facts: [...]` list; dropping")
        return []

    facts: list[Fact] = []
    for i, record in enumerate(records):
        try:
            fact = _record_to_fact(record, file=file, repo_id=repo_id)
        except (ValueError, ValidationError) as exc:
            logger.warning("llm: record #%d invalid (%s); skipping", i, exc)
            continue
        if fact is None:
            continue
        facts.append(fact)
    return facts


def _record_to_fact(record: Any, *, file: str, repo_id: str) -> Fact | None:
    """Coerce one LLM-emitted record into a `Fact`, or None to skip.

    Raises `ValueError` / `ValidationError` only when the record is
    structurally broken (wrong type). A skip (return None) is for
    valid-but-unwanted records like unknown kinds — keeps the caller's
    log noise down."""
    if not isinstance(record, dict):
        raise ValueError(f"expected object, got {type(record).__name__}")
    kind_raw = record.get("kind")
    if not isinstance(kind_raw, str):
        raise ValueError("missing/non-string `kind`")
    if kind_raw not in _ALLOWED_KINDS:
        # Silent skip — the model proposed a category we don't track.
        return None
    line = record.get("line")
    if not isinstance(line, int) or line < 1:
        raise ValueError(f"`line` must be a positive integer, got {line!r}")
    line_end = record.get("line_end")
    if line_end is not None and (not isinstance(line_end, int) or line_end < line):
        # Don't kill the record over a bad line_end — just drop the field.
        line_end = None
    data_raw = record.get("data") or {}
    if not isinstance(data_raw, dict):
        raise ValueError("`data` must be an object")
    # Coerce all keys to str — pydantic's extra='forbid' on Fact applies
    # to the top level, not to data, so we accept whatever shape the
    # prompt requested.
    data = {str(k): v for k, v in data_raw.items()}
    return Fact(
        kind=FactKind(kind_raw),
        file=file,
        line=int(line),
        line_end=int(line_end) if line_end is not None else None,
        repo_id=repo_id,
        data=data,
    )


__all__ = ["parse_extract_facts_response"]
