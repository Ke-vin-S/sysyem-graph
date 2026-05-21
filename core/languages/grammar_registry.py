"""Instantiate Grammar objects from a LanguageLibrary.

`native` profiles import a class via its dotted path and instantiate it.
`llm` profiles create an `LLMGrammar` claiming the language's extensions.
Always includes `ConfigGrammar` for `.yaml`/`.toml`/`.env`/`.properties` —
those aren't owned by any language profile but are still extracted to feed
`config_value` facts.

LLM fallback suffixes: in addition to extensions explicitly marked
`kind: llm` in a profile, the registry adds a stock list of common code
extensions (`.go`, `.rs`, `.kt`, `.ts`, …) so that any language we don't
have a native grammar for still gets a chance at the LLM-extract pipeline.
The list is overridable via the `LLM_FALLBACK_SUFFIXES` env var (CSV).
"""

from __future__ import annotations

import importlib
import logging
import os

from core.languages.library import LanguageLibrary
from core.languages.profile import GrammarKind, LanguageProfile
from core.types.errors import ConfigurationError

logger = logging.getLogger(__name__)

# Extensions that fall through to the LLM by default. Kept here (not in
# prompts.py) so the routing rule and the prompt's language-hint table
# can evolve independently. We deliberately omit `.py`/`.java`/`.c` and
# the other suffixes claimed by native grammars — those wouldn't be
# reached anyway, but listing them would be misleading.
_DEFAULT_LLM_FALLBACK_SUFFIXES = (
    ".go", ".rs", ".rb",
    ".kt", ".kts", ".swift",
    ".ts", ".tsx", ".js", ".jsx",
    ".cs", ".fs", ".scala",
    ".clj", ".cljs",
    ".ex", ".exs", ".erl",
    ".hs", ".lua", ".php",
    ".dart", ".groovy", ".jl", ".nim", ".zig",
    ".cob", ".cbl",
    ".f90", ".f95",
    ".rpg",
)


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

    # Build the fallback LLMGrammar even when no profile is `kind: llm`,
    # so files written in languages we don't have a native grammar for
    # (`.go`, `.rb`, `.ts`, …) still get routed through the LLM. The
    # native grammars sit ahead of this in the list, so the LLM only
    # sees files no native parser claimed.
    fallback_suffixes = _resolve_llm_fallback_suffixes(llm_extensions, native)
    if fallback_suffixes:
        from core.llm import make_llm_client  # noqa: PLC0415

        grammars.append(
            LLMGrammar(
                claimed_suffixes=tuple(sorted(fallback_suffixes)),
                client=make_llm_client(),
            )
        )
    grammars.append(ConfigGrammar())
    return grammars


def _resolve_llm_fallback_suffixes(
    profile_llm_extensions: list[str], native_grammars: list
) -> set[str]:
    """Decide which extensions the fallback LLMGrammar should claim.

    Sources, in precedence order:
      1. Anything a `kind: llm` profile listed.
      2. The stock default list.
      3. `LLM_FALLBACK_SUFFIXES` (CSV) env var fully overrides #2.

    Suffixes already claimed by a native grammar are stripped — the
    walker would never route those to the LLM anyway, but keeping the
    claim set clean makes the `--list-grammars` output less confusing."""
    override = os.environ.get("LLM_FALLBACK_SUFFIXES", "").strip()
    if override:
        base = {s.strip().lower() for s in override.split(",") if s.strip()}
        # Normalise: accept "go" or ".go" indifferently.
        base = {s if s.startswith(".") else f".{s}" for s in base}
    else:
        base = set(_DEFAULT_LLM_FALLBACK_SUFFIXES)
    base.update(profile_llm_extensions)
    # Drop anything a native grammar already claims.
    native_claimed: set[str] = set()
    for grammar in native_grammars:
        native_claimed.update(grammar.suffixes)
    return base - native_claimed


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
