"""EndpointStrategy ABC + registry.

EndpointResolver is a thin dispatcher; the framework-specific knowledge
lives in per-framework strategy classes under
`core/languages/<lang>/extractors/endpoints/`. Strategies register
themselves at import time keyed by their framework name (matches the
`name:` field in `frameworks/<lang>/<framework>.yaml`).

Why this split: a hardcoded `if fw.language == "python"` branch with a
language-specific method made EndpointResolver impossible to extend
without editing it. Strategies invert that: adding Django or Faust is
adding a file under `core/languages/python/extractors/endpoints/`, no
edits to cross-cutting code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.facts import FactTree
from core.frameworks import EffectiveFramework
from core.resolvers.endpoints.types import ResolvedEndpoint


class EndpointStrategy(ABC):
    """One framework's recipe for reconstructing endpoints from facts."""

    @abstractmethod
    def resolve(
        self, *, tree: FactTree, fw: EffectiveFramework, repo_id: str
    ) -> list[ResolvedEndpoint]:
        ...


_STRATEGIES: dict[str, type[EndpointStrategy]] = {}


def register(framework_name: str, cls: type[EndpointStrategy]) -> None:
    """Register a strategy class under a framework name. Idempotent;
    re-registration overwrites (useful in tests)."""
    _STRATEGIES[framework_name] = cls


def get_strategy(framework_name: str) -> EndpointStrategy | None:
    cls = _STRATEGIES.get(framework_name)
    return cls() if cls else None


def registered_frameworks() -> list[str]:
    return sorted(_STRATEGIES)
