"""LLM cost/usage tracking with fail-open behavior.

LLM calls are expensive and bursty. Every LLMClient call passes through a
budget check that either lets the call through or raises BudgetExceeded.
Callers are expected to catch BudgetExceeded and fall back to deterministic
behavior (`fail_open=True` semantics). The class doesn't decide policy — it
only tracks and gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """Raised when a budget cap is hit. Callers should fall back to
    deterministic behavior rather than propagate."""

    def __init__(self, kind: str, limit: float, observed: float) -> None:
        super().__init__(f"LLM {kind} budget exceeded: {observed} > {limit}")
        self.kind = kind
        self.limit = limit
        self.observed = observed


@dataclass
class LLMUsage:
    """Counters incremented as LLM calls happen.

    `cost_dollars` is an estimate; concrete providers fill it in from their
    pricing per-1K-tokens. NullClient leaves it at 0.0.
    """

    files_seen: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0
    cost_dollars: float = 0.0


@dataclass
class LLMBudget:
    """Per-run caps. `fail_open` controls whether a hit is recoverable.

    A run is a single AdapterRegistry.run_all() invocation. The same budget
    instance is shared across all grammars/resolvers/profile-learners that
    might use the LLM, so a 200-file cap applies across the whole run, not
    per-adapter.
    """

    max_files_per_run: int = 200
    max_tokens_per_run: int = 500_000
    max_dollars_per_run: float = 5.00
    fail_open: bool = True
    usage: LLMUsage = field(default_factory=LLMUsage)

    def check(self, *, est_tokens_in: int = 0, est_cost_dollars: float = 0.0) -> None:
        """Raise BudgetExceeded if this call would cross a cap.

        Conservative: refuses the call when adding the estimate would *exceed*
        a limit. Real cost is recorded post-call via `record()`.
        """
        if self.usage.files_seen + 1 > self.max_files_per_run:
            raise BudgetExceeded("files", self.max_files_per_run, self.usage.files_seen + 1)
        if self.usage.tokens_in + est_tokens_in > self.max_tokens_per_run:
            raise BudgetExceeded(
                "tokens", self.max_tokens_per_run, self.usage.tokens_in + est_tokens_in
            )
        if self.usage.cost_dollars + est_cost_dollars > self.max_dollars_per_run:
            raise BudgetExceeded(
                "dollars", self.max_dollars_per_run, self.usage.cost_dollars + est_cost_dollars
            )

    def record(
        self,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_dollars: float = 0.0,
        files: int = 1,
    ) -> None:
        self.usage.files_seen += files
        self.usage.tokens_in += tokens_in
        self.usage.tokens_out += tokens_out
        self.usage.cost_dollars += cost_dollars
        self.usage.calls += 1
