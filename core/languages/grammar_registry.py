"""Instantiate Grammar objects from a LanguageLibrary.

`native` profiles import a class via its dotted path and instantiate it.
`llm` profiles create an `LLMGrammar` claiming the language's extensions.
Always includes `ConfigGrammar` for `.yaml`/`.toml`/`.env`/`.properties` —
those aren't owned by any language profile but are still extracted to feed
`config_value` facts.
"""

from __future__ import annotations

import importlib
import logging

from core.languages.library import LanguageLibrary
from core.languages.profile import GrammarKind, LanguageProfile
from core.types.errors import ConfigurationError

logger = logging.getLogger(__name__)


def build_grammars(library: LanguageLibrary) -> list:
    """Return a list of Grammar instances driven by the library.

    Order matters — the walker tries each grammar's `matches()` in order.
    Native grammars first (specific suffixes), then LLM grammars (any
    remaining), then ConfigGrammar.
    """
    # Imported lazily because the registry sits below grammars in the import
    # graph; avoid circular imports when grammars import from core.facts.
    from ingestion.grammars import ConfigGrammar, Grammar, LLMGrammar

    native: list[Grammar] = []
    llm_extensions: list[str] = []

    for profile in library.all():
        if profile.grammar.kind is GrammarKind.NATIVE:
            native.append(_load_native(profile))
        elif profile.grammar.kind is GrammarKind.LLM:
            llm_extensions.extend(profile.file_extensions)
        else:  # pragma: no cover — exhaustive enum
            raise ConfigurationError(
                f"unknown grammar kind {profile.grammar.kind!r} for language {profile.name}"
            )

    grammars: list[Grammar] = list(native)
    if llm_extensions:
        grammars.append(LLMGrammar(claimed_suffixes=tuple(sorted(set(llm_extensions)))))
    grammars.append(ConfigGrammar())
    return grammars


def _load_native(profile: LanguageProfile):
    driver = profile.grammar.driver
    if not driver:
        raise ConfigurationError(
            f"language {profile.name} has grammar.kind=native but no driver path"
        )
    module_path, _, class_name = driver.rpartition(".")
    if not module_path or not class_name:
        raise ConfigurationError(
            f"language {profile.name}: grammar.driver must be a dotted class path"
        )
    try:
        module = importlib.import_module(module_path)
        grammar_cls = getattr(module, class_name)
    except Exception as exc:
        raise ConfigurationError(
            f"language {profile.name}: failed to load grammar driver {driver!r}: {exc}"
        ) from exc
    return grammar_cls()
