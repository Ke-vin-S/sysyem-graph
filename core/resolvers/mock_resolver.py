"""MockResolver: turn @patch / @patch.object decorators into Mock records.

Driven entirely by framework YAML — `mocks.decorator_callees`,
`mocks.with_callees`, and Mock-related fields on each framework. The
resolver itself doesn't know "patch" or "Mockito"; it knows: "if a
DECORATOR fact's callee matches a configured mock pattern, treat its
first arg as the patch target and try to resolve it locally."

What we handle in v1:
  * @patch("module.thing") and @mock.patch("module.thing")
  * @patch.object(SomeClass, "method")
  * Re-exports through aggregator files (reuses the alias-map machinery
    from CoverageResolver)

Out of scope for v1 (will land later):
  * `with patch(...):` context managers — needs a CONTEXT_ENTER fact
    kind the grammar doesn't emit yet
  * `mocker.patch(...)` pytest-mock fixture — looks like a regular CALL,
    can fold in once we expose call-site mocks at all
  * Anything fancier than literal-string targets
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from core.facts import Fact, FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.languages import LanguageLibrary, LanguageProfile
from core.languages.profile import (
    Grammar,
    GrammarKind,
    ModuleResolution,
    PackageAggregator,
)
from core.languages.resolution import (
    init_file_to_module,
    is_aggregator_file,
    resolve_candidate_files,
)
from core.types import CodeArtifact, Mock, MockKind, TestCase


@dataclass
class MockResolution:
    mocks: list[Mock]


_FALLBACK_PYTHON = LanguageProfile(
    name="python",
    file_extensions=(".py",),
    grammar=Grammar(kind=GrammarKind.NATIVE, driver="core.languages.python.grammar.PythonGrammar"),
    module_resolution=ModuleResolution(
        separator=".",
        candidate_path_templates=("{module}.py", "{module}/__init__.py"),
    ),
    package_aggregator=PackageAggregator(files=("__init__.py",)),
)


class MockResolver:
    PASS_NAME = "mock_resolver"
    _MAX_ALIAS_DEPTH = 5

    def resolve(
        self,
        *,
        tree: FactTree,
        tests: Iterable[TestCase],
        artifacts: Iterable[CodeArtifact],
        frameworks: tuple[EffectiveFramework, ...],
        repo_id: str,
        repo_root: str | None = None,
        languages: LanguageLibrary | None = None,
    ) -> MockResolution:
        callees = _merge_mock_callees(frameworks)
        if not callees:
            return MockResolution(mocks=[])

        # Index tests by (file, function_name) so we can attribute each
        # decorator's `target_symbol` back to a TestCase id.
        test_index: dict[tuple[str, str], TestCase] = {
            (t.file, t.name): t for t in tests
        }

        artifacts_list = list(artifacts)
        by_file_name: dict[tuple[str, str], CodeArtifact] = {
            (a.file, a.name): a for a in artifacts_list
        }
        # For patch.object, we need to resolve methods by (Class.method).
        by_method_qualifier: dict[str, CodeArtifact] = {
            f"{_enclosing_class_for(a)}.{a.name}": a
            for a in artifacts_list
            if a.type == "method"
        }

        alias_map = self._build_alias_map(tree, repo_root, languages)

        mocks: list[Mock] = []
        seen_ids: set[str] = set()
        for dec in tree.where(kind=FactKind.DECORATOR):
            callee = str(dec.data.get("callee", ""))
            if callee not in callees:
                continue
            target_symbol = str(dec.data.get("target_symbol", ""))
            if not target_symbol:
                continue
            dec_file = _rel_to(dec.file, repo_root) if repo_root else dec.file
            test = test_index.get((dec_file, target_symbol))
            if test is None:
                continue  # decorator wraps a non-test (skip — not our concern)

            profile = self._profile_for(dec_file, languages)
            mock = self._build_mock(
                dec=dec,
                dec_file=dec_file,
                callee=callee,
                test=test,
                repo_id=repo_id,
                profile=profile,
                alias_map=alias_map,
                by_file_name=by_file_name,
                by_method_qualifier=by_method_qualifier,
            )
            if mock is None:
                continue
            if mock.id in seen_ids:
                continue
            seen_ids.add(mock.id)
            mocks.append(mock)

        return MockResolution(mocks=mocks)

    # -- per-decorator building ------------------------------------------

    def _build_mock(
        self,
        *,
        dec: Fact,
        dec_file: str,
        callee: str,
        test: TestCase,
        repo_id: str,
        profile: LanguageProfile,
        alias_map: dict[tuple[str, str], list[tuple[str, str]]],
        by_file_name: dict[tuple[str, str], CodeArtifact],
        by_method_qualifier: dict[str, CodeArtifact],
    ) -> Mock | None:
        args = dec.data.get("args") or []
        if callee.endswith("patch.object") or callee == "patch.object":
            # @patch.object(SomeClass, "method")
            if len(args) < 2:
                return None
            class_token, method = args[0], args[1]
            if not isinstance(method, str):
                return None
            class_name = _strip_token(class_token)
            if not class_name:
                return None
            target = f"{class_name}.{method}"
            target_artifact_id = by_method_qualifier.get(target)
            return Mock(
                id=f"mock:{repo_id}:{test.id}:{target}",
                repoId=repo_id,
                testId=test.id,
                kind=MockKind.PATCH_OBJECT,
                patchTarget=target,
                targetArtifactId=target_artifact_id.id if target_artifact_id else None,
                file=dec_file,
                line=dec.line,
                producedBy=self.PASS_NAME,
                fromFacts=(dec.id,),
            )

        # @patch("module.thing") or @mock.patch("module.thing")
        if not args or not isinstance(args[0], str):
            return None
        target = args[0]
        module, _, name = target.rpartition(".")
        target_artifact_id: str | None = None
        if module and name:
            for real_module, real_name in self._expand(module, name, alias_map):
                for candidate_file in resolve_candidate_files(real_module, profile):
                    hit = by_file_name.get((candidate_file, real_name))
                    if hit is not None:
                        target_artifact_id = hit.id
                        break
                if target_artifact_id:
                    break
        return Mock(
            id=f"mock:{repo_id}:{test.id}:{target}",
            repoId=repo_id,
            testId=test.id,
            kind=MockKind.PATCH_STR,
            patchTarget=target,
            targetArtifactId=target_artifact_id,
            file=dec_file,
            line=dec.line,
            producedBy=self.PASS_NAME,
            fromFacts=(dec.id,),
        )

    # -- alias map (same shape as CoverageResolver/FunctionCallResolver) -

    def _build_alias_map(
        self,
        tree: FactTree,
        repo_root: str | None,
        languages: LanguageLibrary | None,
    ) -> dict[tuple[str, str], list[tuple[str, str]]]:
        aliases: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for fact in tree.where(kind=FactKind.IMPORT):
            file = _rel_to(fact.file, repo_root) if repo_root else fact.file
            profile = self._profile_for(file, languages)
            if not is_aggregator_file(file, profile):
                continue
            package_module = init_file_to_module(file, profile)
            if not package_module:
                continue
            target_module = self._resolve_target_module(fact, package_module, profile)
            if not target_module:
                continue
            for name in fact.data.get("names") or []:
                if not name:
                    continue
                aliases.setdefault((package_module, name), []).append(
                    (target_module, name)
                )
        return aliases

    def _resolve_target_module(
        self, import_fact: Fact, package_module: str, profile: LanguageProfile
    ) -> str:
        target_module = str(import_fact.data.get("module", ""))
        level = int(import_fact.data.get("level", 0) or 0)
        if level <= 0:
            return target_module
        sep = profile.module_resolution.separator or "."
        parts = package_module.split(sep)
        if level > len(parts):
            return target_module
        anchor = sep.join(parts[: len(parts) - level + 1])
        if target_module:
            return f"{anchor}{sep}{target_module}"
        return anchor

    def _expand(
        self,
        module: str,
        name: str,
        alias_map: dict[tuple[str, str], list[tuple[str, str]]],
    ) -> Iterable[tuple[str, str]]:
        seen: set[tuple[str, str]] = set()
        stack: list[tuple[str, str, int]] = [(module, name, 0)]
        while stack:
            mod, nm, depth = stack.pop()
            key = (mod, nm)
            if key in seen or depth > self._MAX_ALIAS_DEPTH:
                continue
            seen.add(key)
            yield key
            for next_mod, next_name in alias_map.get(key, []):
                stack.append((next_mod, next_name, depth + 1))

    def _profile_for(
        self, file: str, languages: LanguageLibrary | None
    ) -> LanguageProfile:
        if languages is not None:
            profile = languages.for_file(file)
            if profile is not None:
                return profile
        return _FALLBACK_PYTHON


def _merge_mock_callees(frameworks: tuple[EffectiveFramework, ...]) -> set[str]:
    """Union of `mocks.decorator_callees` across all detected frameworks."""
    callees: set[str] = set()
    for fw in frameworks:
        if fw.mocks is not None:
            callees.update(fw.mocks.decorator_callees)
    return callees


def _enclosing_class_for(artifact: CodeArtifact) -> str:
    """Method artifact IDs are `method:repo:file:Class.name`. Parse out the
    Class qualifier so we can build a lookup map for patch.object."""
    last = artifact.id.rsplit(":", 1)[-1]
    return last.split(".", 1)[0] if "." in last else ""


def _strip_token(value: Any) -> str:
    """Pull the class name out of a literal_value placeholder.

    `<name:SomeClass>`        -> "SomeClass"
    `<attr:mymod.SomeClass>`  -> "SomeClass"
    plain string              -> "" (we expect a name/attr, not a string)
    """
    if not isinstance(value, str):
        return ""
    if value.startswith("<name:") and value.endswith(">"):
        return value[len("<name:") : -1]
    if value.startswith("<attr:") and value.endswith(">"):
        return value[len("<attr:") : -1].rsplit(".", 1)[-1]
    return ""


def _rel_to(file: str, root: str) -> str:
    if not root:
        return file
    fp = PurePosixPath(file.replace("\\", "/"))
    rp = PurePosixPath(root.replace("\\", "/"))
    try:
        return str(fp.relative_to(rp))
    except ValueError:
        parts = fp.parts
        root_name = rp.name
        if root_name in parts:
            idx = parts.index(root_name)
            return str(PurePosixPath(*parts[idx + 1 :]))
        return file
