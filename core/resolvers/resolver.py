"""Common context for all resolvers.

Resolvers take a `ResolverContext` (tree + effective frameworks + repo_id)
and produce typed records. They never mutate the tree or the framework
definitions. The same context is reused across resolvers within one repo,
so framework detection and overlay composition happen once per repo.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.facts import FactTree
from core.frameworks import EffectiveFramework


@dataclass
class ResolverContext:
    tree: FactTree
    frameworks: tuple[EffectiveFramework, ...]
    repo_id: str

    def frameworks_for_language(self, language: str) -> tuple[EffectiveFramework, ...]:
        return tuple(fw for fw in self.frameworks if fw.language == language)

    def framework(self, name: str) -> EffectiveFramework | None:
        for fw in self.frameworks:
            if fw.name == name:
                return fw
        return None
