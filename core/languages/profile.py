"""Schema for `core/languages/<lang>/profile.yaml`.

Each YAML file declares one language. Resolvers read this — never code.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    """Mirrors `core.types.service._Frozen`: immutable, strict, alias-friendly."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class GrammarKind(StrEnum):
    NATIVE = "native"
    """A Python class implementing `Grammar` extracts Facts from source.
    Driver is a dotted import path (e.g. `core.languages.python.grammar.PythonGrammar`)."""

    LLM = "llm"
    """No native parser exists; `LLMGrammar` calls an `LLMClient`. With the
    default `NullClient` this returns no facts — but the file routes
    cleanly without exceptions, so PL/SQL files don't crash the pipeline."""


class Grammar(_Frozen):
    kind: GrammarKind = GrammarKind.NATIVE
    driver: str = ""
    """For `kind=native`, dotted import path to a `Grammar` subclass.
    Empty for `kind=llm`."""

    prompt_version: str = "extract-facts-v1"
    """For `kind=llm`, the prompt version key the cache and providers use."""


class ModuleResolution(_Frozen):
    """How a dotted module name expands to candidate file paths.

    Python: separator=".", templates=["{module}.py", "{module}/__init__.py"]
    Java:   separator=".", templates=["{module}.java"]
    PL/SQL: separator=".", templates=["{module}.pks", "{module}.pkb", "{module}.sql"]
    """

    separator: str = "."
    candidate_path_templates: tuple[str, ...] = Field(default_factory=tuple)


class PackageAggregator(_Frozen):
    """File patterns where re-exports live.

    Python: ["__init__.py"]
    TypeScript: ["index.ts", "index.tsx"]
    PL/SQL: ["*.pks"] — spec files declare what the body exports
    Java: []
    """

    files: tuple[str, ...] = Field(default_factory=tuple)


class Visibility(_Frozen):
    rule: str = "always_public"
    """Name of a registered VisibilityRule (see core.languages.visibility)."""


class TestPaths(_Frozen):
    glob_patterns: tuple[str, ...] = Field(default_factory=tuple)
    """Glob patterns (segment-aware, relative paths) that identify test files.

    Conventions:
      ``**/segment/**`` — match if any path segment equals ``segment``
      ``**/pattern``    — match by basename via fnmatch
      ``literal``       — exact-match fnmatch over the full path
    """

    function_name_prefixes: tuple[str, ...] = Field(default_factory=tuple)
    """Identifier prefixes that mark a function as a test in this language's
    most common convention (e.g. `test_` for Python pytest, empty for Java
    since JUnit relies on annotations). Framework YAMLs can add to this set
    via their own `tests.function_name_prefixes`, but language YAML provides
    the baseline so test detection works even without a detected framework."""


class LanguageProfile(_Frozen):
    name: str
    file_extensions: tuple[str, ...]
    grammar: Grammar = Field(default_factory=Grammar)
    module_resolution: ModuleResolution = Field(default_factory=ModuleResolution)
    package_aggregator: PackageAggregator = Field(default_factory=PackageAggregator)
    visibility: Visibility = Field(default_factory=Visibility)
    test_paths: TestPaths = Field(default_factory=TestPaths)
    notes: str = ""

    def claims(self, file: str) -> bool:
        """True iff this profile's extensions cover `file`."""
        return any(file.endswith(ext) for ext in self.file_extensions)
