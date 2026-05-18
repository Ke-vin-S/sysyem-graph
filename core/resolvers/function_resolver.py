"""FunctionResolver: emit CodeArtifact records for the code structure map.

For every top-level public function, class, and method, emit a CodeArtifact.
Combined with EndpointResolver, this is the "what's in this repo?" view —
the substrate the impact engine queries against.

Visibility is driven by `core.languages` — each language profile names a
rule (`leading_underscore_is_private` for Python, `java_public_modifier`
for Java, etc.). The hardcoded `name.startswith('_')` check is gone.

Test files are excluded — `TestResolver` handles those.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from core.facts import Fact, FactKind
from core.languages import LanguageLibrary
from core.languages import is_public as visibility_is_public
from core.resolvers.resolver import ResolverContext
from core.types import CodeArtifact, LineRange


class FunctionResolver:
    PASS_NAME = "function_resolver"

    def resolve(self, context: ResolverContext) -> list[CodeArtifact]:
        out: list[CodeArtifact] = []
        seen: set[str] = set()
        for fact in context.tree:
            if fact.kind is FactKind.CLASS_DEF:
                artifact = self._class_artifact(fact, context)
            elif fact.kind is FactKind.SYMBOL:
                artifact = self._symbol_artifact(fact, context)
            else:
                continue
            if artifact is None or artifact.id in seen:
                continue
            seen.add(artifact.id)
            out.append(artifact)
        return out

    def _class_artifact(self, fact: Fact, ctx: ResolverContext) -> CodeArtifact | None:
        if _is_test_path(fact.file, ctx.languages):
            return None
        name = str(fact.data.get("name", ""))
        if not name:
            return None
        return CodeArtifact(
            id=f"class:{ctx.repo_id}:{fact.file}:{name}",
            repoId=ctx.repo_id,
            type="class",
            name=name,
            file=fact.file,
            lineRange=LineRange(start=fact.line, end=fact.line_end or fact.line),
            isPublic=_is_public(fact, ctx.languages),
            producedBy=self.PASS_NAME,
            fromFacts=(fact.id,),
        )

    def _symbol_artifact(self, fact: Fact, ctx: ResolverContext) -> CodeArtifact | None:
        if _is_test_path(fact.file, ctx.languages):
            return None
        sym_kind = str(fact.data.get("sym_kind", ""))
        name = str(fact.data.get("name", ""))
        if not name:
            return None
        enclosing = str(fact.data.get("enclosing_class", ""))
        if sym_kind == "function":
            type_label = "function"
            artifact_id = f"fn:{ctx.repo_id}:{fact.file}:{name}"
        elif sym_kind == "method":
            type_label = "method"
            artifact_id = f"method:{ctx.repo_id}:{fact.file}:{enclosing}.{name}"
        elif sym_kind == "field":
            return None
        else:
            return None
        return CodeArtifact(
            id=artifact_id,
            repoId=ctx.repo_id,
            type=type_label,
            name=name,
            file=fact.file,
            lineRange=LineRange(start=fact.line, end=fact.line_end or fact.line),
            isPublic=_is_public(fact, ctx.languages),
            producedBy=self.PASS_NAME,
            fromFacts=(fact.id,),
        )


def _is_public(fact: Fact, languages: LanguageLibrary | None) -> bool:
    """Consult the language profile's visibility rule. Fallback: Python
    leading-underscore convention for backward compatibility."""
    if languages is not None:
        profile = languages.for_file(fact.file)
        if profile is not None:
            return visibility_is_public(profile.visibility.rule, fact)
    name = str(fact.data.get("name", ""))
    return bool(name) and not name.startswith("_")


def _is_test_path(file: str, languages: LanguageLibrary | None) -> bool:
    """A file is a test path iff any registered language profile's
    `test_paths.glob_patterns` matches it."""
    if languages is not None:
        profile = languages.for_file(file)
        if profile is not None and profile.test_paths.glob_patterns:
            return _matches_test_pattern(file, profile.test_paths.glob_patterns)
    parts = Path(file).parts
    return any(part in {"tests", "test"} for part in parts)


def _matches_test_pattern(file: str, patterns: tuple[str, ...]) -> bool:
    """Segment-aware matcher for the test-path glob conventions.

    `fnmatch` treats `*` as matching across `/`, so a path like
    `/tmp/test_run/src/x.py` would match `**/test_*.py` (the directory name
    `test_run` triggers it). We avoid that by parsing the convention manually:

      **/segment/**   ->  some path segment equals `segment`
      **/filename     ->  the file's basename matches `filename` via fnmatch
      anything else   ->  fall back to full-path fnmatch
    """
    path = Path(file)
    parts = set(path.parts)
    name = path.name
    for pattern in patterns:
        if pattern.startswith("**/") and pattern.endswith("/**"):
            segment = pattern[3:-3]
            if segment in parts:
                return True
        elif pattern.startswith("**/"):
            filename_pattern = pattern[3:]
            if fnmatch.fnmatch(name, filename_pattern):
                return True
        elif fnmatch.fnmatch(file, pattern):
            return True
    return False
