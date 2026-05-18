"""Language knowledge as declarative config.

A `LanguageProfile` (loaded from `languages/<lang>.yaml`) captures everything
the system used to assume about Python:

  * file extensions claimed by this language
  * how to extract Facts from its source (native Grammar vs LLM)
  * how dotted module names resolve to file paths
  * where re-exports live (the "package aggregator" — `__init__.py` for
    Python, `index.ts` for TypeScript, `.pks` spec files for PL/SQL)
  * how to decide whether a symbol is public
  * test-path glob conventions

Adding a new language is a single YAML drop. If no native AST library exists
for it (e.g. PL/SQL), `grammar.kind: llm` routes its files through
`LLMGrammar` — today's NullClient returns no facts, so the pipeline stays
clean while waiting for a real provider.
"""

from core.languages.library import LanguageLibrary, load_library
from core.languages.profile import (
    Grammar,
    GrammarKind,
    LanguageProfile,
    ModuleResolution,
    PackageAggregator,
    TestPaths,
    Visibility,
)
from core.languages.resolution import init_file_to_module, resolve_candidate_files
from core.languages.visibility import VisibilityRule, is_public, registered_rules

__all__ = [
    "Grammar",
    "GrammarKind",
    "LanguageLibrary",
    "LanguageProfile",
    "ModuleResolution",
    "PackageAggregator",
    "TestPaths",
    "Visibility",
    "VisibilityRule",
    "init_file_to_module",
    "is_public",
    "load_library",
    "registered_rules",
    "resolve_candidate_files",
]
