"""Grammar implementations — language-aware fact extractors.

Each Grammar reads one source file and emits a list of `Fact` records. The
output is structural and uninterpreted; resolvers (`core/resolvers/`) join
the facts across files and consult framework YAML to produce meaning.
"""

from ingestion.grammars.config_grammar import ConfigGrammar
from ingestion.grammars.grammar import Grammar
from ingestion.grammars.java_grammar import JavaGrammar
from ingestion.grammars.llm_grammar import LLMGrammar
from ingestion.grammars.python_grammar import PythonGrammar

__all__ = ["ConfigGrammar", "Grammar", "JavaGrammar", "LLMGrammar", "PythonGrammar"]
