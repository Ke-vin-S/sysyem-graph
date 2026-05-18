"""Common context for all resolvers.

Resolvers take a `ResolverContext` (tree + frameworks + language library +
repo_id) and produce typed records. They never mutate the tree, the
framework definitions, or the language library. The same context is reused
across resolvers within one repo, so framework detection and overlay
composition happen once per repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.facts import FactTree
from core.frameworks import EffectiveFramework
from core.languages import LanguageLibrary


def _default_languages() -> LanguageLibrary:
    """Lazy default. Returns an empty library if `languages/` is missing,
    so unit tests that don't care about language profiles still work."""
    try:
        from core.languages import load_library

        return load_library()
    except Exception:
        return LanguageLibrary()


@dataclass
class ResolverContext:
    tree: FactTree
    frameworks: tuple[EffectiveFramework, ...]
    repo_id: str
    languages: LanguageLibrary = field(default_factory=_default_languages)

    def frameworks_for_language(self, language: str) -> tuple[EffectiveFramework, ...]:
        return tuple(fw for fw in self.frameworks if fw.language == language)

    def framework(self, name: str) -> EffectiveFramework | None:
        for fw in self.frameworks:
            if fw.name == name:
                return fw
        return None
