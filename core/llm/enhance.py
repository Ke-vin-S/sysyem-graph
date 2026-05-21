"""LLM enhance pass — propose candidate edges for ambiguities the
deterministic resolvers couldn't bind.

Design contract:
  * Reads a MergedResult; writes Suggestion records onto it.
  * NEVER overrides resolver-emitted edges. LLM output is a parallel
    layer; queries can filter to `source="resolver"` to ignore it.
  * Cached by SHA-256(prompt_version, prompt_body, model) — re-runs on
    unchanged input are free.
  * No-op when the client is NullClient (no provider configured).
    Pipeline doesn't break; you just get zero suggestions.

v1 questions asked:
  * "Unresolved function call": for each CALL fact whose receiver+method
    didn't match any local artifact, give the LLM the candidate target
    list and ask which one (if any) the call refers to.

Future passes (each is a separate `_propose_*` method on the enhancer
following the same shape):
  * Unresolved config bindings (key referenced in code with no matching
    CONFIG_VALUE).
  * Endpoint→DataModel READ/WRITE inference from handler bodies.
  * Cross-repo Kafka topic naming variants ("user.events" vs "UserEvents").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.adapters.merger import MergedResult
from core.facts import Fact, FactKind, FactTree
from core.llm.cache import LLMCache, NullCache, cache_key
from core.llm.client import LLMClient, SubgraphQuestion
from core.llm.null_client import NullClient
from core.types import Suggestion

logger = logging.getLogger(__name__)


_PROMPT_VERSION = "enhance-v1"


@dataclass
class EnhanceStats:
    proposals_made: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    accepted: int = 0
    """Proposals the LLM accepted (returned a target with confidence > 0)."""
    questions_asked: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class LLMEnhancer:
    client: LLMClient
    cache: LLMCache
    min_confidence: float = 0.6
    """Suggestions below this threshold are dropped."""

    model_name: str = "claude-haiku-4-5"
    """Tag stamped on emitted Suggestions for audit. Real provider may
    override (e.g. when the AnthropicClient negotiates a different model)."""

    @classmethod
    def null(cls) -> LLMEnhancer:
        """Default no-op enhancer: NullClient + NullCache, returns 0 suggestions.
        Useful as the default in adapters/CLIs so the pipeline never breaks
        when no provider is configured."""
        return cls(client=NullClient(), cache=NullCache())

    def enhance(self, merged: MergedResult, tree: FactTree) -> EnhanceStats:
        stats = EnhanceStats()
        # Skip silently when no real provider — keeps the contract simple.
        if isinstance(self.client, NullClient):
            stats.notes.append("NullClient — no suggestions generated")
            return stats

        for suggestion in self._propose_unresolved_calls(merged, tree, stats):
            merged.suggestions.setdefault(suggestion.id, suggestion)
        return stats

    # ---- pass 1: unresolved function calls ------------------------------

    def _propose_unresolved_calls(
        self, merged: MergedResult, tree: FactTree, stats: EnhanceStats
    ) -> list[Suggestion]:
        """Find CALL facts whose target isn't already in any artifact's
        `calls` field. Ask the LLM to bind them to a candidate."""
        artifact_index = {a.id: a for a in merged.artifacts.values()}
        # For each caller, the set of bare names it already calls (via
        # resolver binding). We use bare names because the CALL fact's
        # `method`/`callee` is by-name, not by-id.
        bound_names_by_caller: dict[str, set[str]] = {}
        for art in merged.artifacts.values():
            names: set[str] = set()
            for callee_id in art.calls:
                target_art = artifact_index.get(callee_id)
                if target_art is not None:
                    names.add(target_art.name)
            if names:
                bound_names_by_caller[art.id] = names

        suggestions: list[Suggestion] = []
        candidates_summary = _candidates_summary(merged.artifacts.values())

        for call_fact in tree.where(kind=FactKind.CALL):
            callee = str(call_fact.data.get("callee", ""))
            if not callee:
                continue
            enclosing = _enclosing_artifact(merged.artifacts.values(), call_fact)
            if enclosing is None:
                continue
            already = bound_names_by_caller.get(enclosing.id, frozenset())
            method = str(call_fact.data.get("method", "")) or _last(callee)
            if method in already:
                continue  # resolver already covered this call

            suggestion = self._ask_for_call_target(
                caller=enclosing,
                call_fact=call_fact,
                candidates_summary=candidates_summary,
                artifact_index=artifact_index,
                stats=stats,
            )
            if suggestion is not None:
                suggestions.append(suggestion)
        return suggestions

    def _ask_for_call_target(
        self,
        *,
        caller,
        call_fact: Fact,
        candidates_summary: str,
        artifact_index,
        stats: EnhanceStats,
    ) -> Suggestion | None:
        callee_str = str(call_fact.data.get("callee", ""))
        prompt = (
            f"A function call in {call_fact.file}:{call_fact.line} writes "
            f"`{callee_str}`. The enclosing function is `{caller.name}` "
            f"({caller.file}). Pick the target from the candidates below, or "
            f"reply with target_id=null if none apply.\n\n"
            f"Candidates:\n{candidates_summary}\n"
        )
        key = cache_key(prompt_version=_PROMPT_VERSION, content=prompt, extra=self.model_name)
        cached = self.cache.get(key)
        if cached is not None:
            stats.cache_hits += 1
            return _suggestion_from_response(cached, caller=caller, call_fact=call_fact,
                                             artifact_index=artifact_index,
                                             min_confidence=self.min_confidence,
                                             model=self.model_name)
        stats.cache_misses += 1
        stats.questions_asked += 1
        try:
            resolution = self.client.resolve_subgraph(
                SubgraphQuestion(
                    question=prompt,
                    facts=[call_fact],
                    expected_schema={"target_id": "string|null", "confidence": "float", "reason": "string"},
                )
            )
        except Exception as exc:
            logger.warning("llm enhance: provider error, skipping: %s", exc)
            return None
        response: dict[str, Any] = {
            "target_id": resolution.answer.get("target_id"),
            "confidence": resolution.confidence,
            "reason": resolution.notes,
        }
        self.cache.put(key, response)
        stats.proposals_made += 1
        return _suggestion_from_response(response, caller=caller, call_fact=call_fact,
                                         artifact_index=artifact_index,
                                         min_confidence=self.min_confidence,
                                         model=self.model_name)


def _suggestion_from_response(
    response: dict[str, Any],
    *,
    caller,
    call_fact: Fact,
    artifact_index,
    min_confidence: float,
    model: str,
) -> Suggestion | None:
    target_id = response.get("target_id")
    if not target_id or target_id not in artifact_index:
        return None
    confidence = float(response.get("confidence") or 0.0)
    if confidence < min_confidence:
        return None
    reason = str(response.get("reason") or "")
    suggestion_id = f"sug:CALLS:{caller.id}:{target_id}"
    return Suggestion(
        id=suggestion_id,
        srcId=caller.id, srcLabel="CodeArtifact",
        rel="CALLS",
        dstId=target_id, dstLabel="CodeArtifact",
        confidence=confidence,
        reason=reason or f"llm-bound {call_fact.data.get('callee')} -> {target_id}",
        promptVersion=_PROMPT_VERSION,
        model=model,
    )


def _enclosing_artifact(artifacts, call_fact: Fact):
    best = None
    best_span = float("inf")
    for art in artifacts:
        if art.type not in ("function", "method"):
            continue
        if art.file != call_fact.file:
            # Try suffix-match — call.file may be absolute, artifact relative.
            if not call_fact.file.endswith(art.file):
                continue
        if not (art.line_range.start <= call_fact.line <= art.line_range.end):
            continue
        span = art.line_range.end - art.line_range.start
        if span < best_span:
            best_span = span
            best = art
    return best


def _candidates_summary(artifacts) -> str:
    # Compact view for the prompt — id + file. Cap at 200 to keep token cost bounded.
    fns = [a for a in artifacts if a.type in ("function", "method")]
    fns.sort(key=lambda a: a.id)
    rows = [f"- {a.id}" for a in fns[:200]]
    if len(fns) > 200:
        rows.append(f"... and {len(fns) - 200} more (truncated)")
    return "\n".join(rows)


def _last(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1]
