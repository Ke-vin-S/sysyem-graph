"""LLM abstraction: interfaces, NullClient, budgets, content-hash cache.

This package ships interfaces and a no-op default. The Anthropic (or other
provider) implementation lands in a follow-up; the slots exist so resolvers
and grammars can reference `LLMClient` without forcing a real LLM dependency.
"""

from core.llm.budgets import BudgetExceeded, LLMBudget, LLMUsage
from core.llm.cache import FileCache, LLMCache, NullCache
from core.llm.client import (
    LLMClient,
    LLMResponse,
    ProfileSample,
    RepoOverlay,
    SubgraphQuestion,
    SubgraphResolution,
)
from core.llm.enhance import EnhanceStats, LLMEnhancer
from core.llm.factory import make_llm_client
from core.llm.null_client import NullClient

__all__ = [
    "BudgetExceeded",
    "EnhanceStats",
    "FileCache",
    "LLMBudget",
    "LLMCache",
    "LLMClient",
    "LLMEnhancer",
    "LLMResponse",
    "LLMUsage",
    "NullCache",
    "NullClient",
    "ProfileSample",
    "RepoOverlay",
    "SubgraphQuestion",
    "SubgraphResolution",
    "make_llm_client",
]
