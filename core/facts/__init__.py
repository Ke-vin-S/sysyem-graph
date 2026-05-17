"""Fact extraction layer: atomic facts collected by grammars before resolvers interpret them."""

from core.facts.fact import Fact, FactKind
from core.facts.tree import FactTree

__all__ = ["Fact", "FactKind", "FactTree"]
