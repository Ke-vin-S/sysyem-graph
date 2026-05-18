"""Cross-language grammar pieces.

The `Grammar` ABC and the language-agnostic grammars (`ConfigGrammar` for
YAML/JSON/properties, `LLMGrammar` for files without a native parser) live
here. Language-specific grammars live under `core/languages/<lang>/grammar.py`
and are wired in via the `grammar.driver` field in each language's
`profile.yaml`.
"""

from ingestion.grammars.config_grammar import ConfigGrammar
from ingestion.grammars.grammar import Grammar
from ingestion.grammars.llm_grammar import LLMGrammar

__all__ = ["ConfigGrammar", "Grammar", "LLMGrammar"]
