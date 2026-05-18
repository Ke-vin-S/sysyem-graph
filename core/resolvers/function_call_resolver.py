"""FunctionCallResolver: emit (CodeArtifact)-[:CALLS]->(CodeArtifact) edges.

For each CALL fact we identify (a) the *caller* — the function/method whose
body contains the call site — and (b) the *callee* — the artifact the call
targets, resolved through the calling file's IMPORT facts plus the language
profile's module_resolution.

Scope intentionally bounded for v1:
  * Same-file calls: `bar()` inside `foo()` where `def bar` lives in the
    same file.
  * Direct-name imports: `from mymod import bar; bar()`.
  * Module-attribute calls: `import mymod; mymod.bar()` (incl. aliases
    `import mymod as m; m.bar()`).
  * Re-exports through aggregator files (Python `__init__.py`, TS
    `index.ts`, PL/SQL `*.pks`) — reuses the alias-map machinery from
    `CoverageResolver`.

Out of scope (left for later, will hit ~15% of real call sites):
  * `self.method()` and instance-method dispatch (needs scope analysis).
  * Method chains: `Foo().bar()`, `get_thing().run()`.
  * Calls through callable objects assigned to local variables.
  * Builtins, third-party modules with no local artifact (filtered out
    silently — there's nothing to link to).

The resolver mutates each input CodeArtifact, populating its `calls` tuple
with target artifact IDs. The adapter then emits these as Neo4j edges via
`GraphLoader`. We also return the raw edge list for debugging / audit.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from core.facts import Fact, FactKind, FactTree
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
from core.types import CodeArtifact


@dataclass
class CallEdge:
    """A resolved (caller -> callee) link for inspection/debugging."""

    caller_id: str
    callee_id: str
    reason: str


@dataclass
class CallResolution:
    artifacts: list[CodeArtifact]
    """The input artifacts, with `calls` populated."""

    edges: list[CallEdge]
    """Flat edge list. Same information as the artifacts' `calls` fields."""


#: Fallback for unit tests that don't pass a LanguageLibrary. Mirrors
#: `CoverageResolver._FALLBACK_PYTHON` so behavior is identical.
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


class FunctionCallResolver:
    _MAX_ALIAS_DEPTH = 5

    def resolve(
        self,
        *,
        tree: FactTree,
        artifacts: Iterable[CodeArtifact],
        repo_root: str | None = None,
        languages: LanguageLibrary | None = None,
    ) -> CallResolution:
        artifacts_list = list(artifacts)
        # Index by file → sorted list of (start, end, artifact) for enclosing lookup.
        by_file_ranges: dict[str, list[tuple[int, int, CodeArtifact]]] = {}
        # Index by (file, name) for callee lookup.
        by_file_name: dict[tuple[str, str], CodeArtifact] = {}
        # Index methods by (file, class, name) so `Class.method` lookups —
        # used for self/cls dispatch and typed-parameter resolution — are O(1).
        methods_by_qualifier: dict[tuple[str, str, str], CodeArtifact] = {}
        for art in artifacts_list:
            by_file_ranges.setdefault(art.file, []).append(
                (art.line_range.start, art.line_range.end, art)
            )
            by_file_name[(art.file, art.name)] = art
            if art.type == "method":
                class_name = _class_of_method(art)
                if class_name:
                    methods_by_qualifier[(art.file, class_name, art.name)] = art

        alias_map = self._build_alias_map(tree, repo_root, languages)
        params_by_artifact_id = self._build_params_index(
            tree, repo_root, repo_id_of(artifacts_list)
        )

        # Per-artifact callee accumulator (preserve insertion order).
        callees: dict[str, list[str]] = {a.id: [] for a in artifacts_list}
        seen_pairs: set[tuple[str, str]] = set()
        edges: list[CallEdge] = []

        for call in tree.where(kind=FactKind.CALL):
            call_file = _rel_to(call.file, repo_root) if repo_root else call.file
            enclosing = _enclosing_artifact(by_file_ranges, call_file, call.line)
            if enclosing is None:
                continue  # module-level call: no caller, skip

            callee_artifact = self._resolve_call_target(
                call=call,
                call_file=call_file,
                tree=tree,
                repo_root=repo_root,
                languages=languages,
                alias_map=alias_map,
                by_file_name=by_file_name,
                enclosing=enclosing,
                methods_by_qualifier=methods_by_qualifier,
                params_by_artifact_id=params_by_artifact_id,
            )
            if callee_artifact is None:
                continue
            if callee_artifact.id == enclosing.id:
                continue  # recursive self-call: edge would be a self-loop

            pair = (enclosing.id, callee_artifact.id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            callees[enclosing.id].append(callee_artifact.id)
            edges.append(
                CallEdge(
                    caller_id=enclosing.id,
                    callee_id=callee_artifact.id,
                    reason=_call_reason(call),
                )
            )

        updated = [
            art.model_copy(update={"calls": tuple(callees.get(art.id, ()))})
            if callees.get(art.id)
            else art
            for art in artifacts_list
        ]
        return CallResolution(artifacts=updated, edges=edges)

    # ---- call-target resolution ----------------------------------------

    def _resolve_call_target(
        self,
        *,
        call: Fact,
        call_file: str,
        tree: FactTree,
        repo_root: str | None,
        languages: LanguageLibrary | None,
        alias_map: dict[tuple[str, str], list[tuple[str, str]]],
        by_file_name: dict[tuple[str, str], CodeArtifact],
        enclosing: CodeArtifact,
        methods_by_qualifier: dict[tuple[str, str, str], CodeArtifact],
        params_by_artifact_id: dict[str, dict[str, str]],
    ) -> CodeArtifact | None:
        receiver = str(call.data.get("receiver", ""))
        method = str(call.data.get("method", ""))
        if not method:
            return None

        profile = self._profile_for(call_file, languages)
        file_imports = self._file_import_index(tree, call_file, repo_root)

        if not receiver:
            # `bar()` — either defined in same file, or imported as a name.
            same_file = by_file_name.get((call_file, method))
            if same_file is not None:
                return same_file
            # Look for `from MOD import method` in this file.
            for module, name, _level in file_imports.from_names:
                if name != method:
                    continue
                target = self._lookup_by_module(
                    module, method, profile, alias_map, by_file_name
                )
                if target is not None:
                    return target
            return None

        # `self.method()` / `cls.method()` — dispatch to a sibling method on
        # the enclosing class. Works for single class; doesn't yet walk MRO.
        if receiver in ("self", "cls") and enclosing.type == "method":
            class_name = _class_of_method(enclosing)
            if class_name:
                target = methods_by_qualifier.get((enclosing.file, class_name, method))
                if target is not None:
                    return target
            return None

        # `receiver.method()` — receiver may be a module alias, a real module,
        # or an unresolved name (e.g. a local variable / `self`).
        # Step 1: dotted-path resolution against bare imports — handles
        # `import a.b.c; a.b.c.foo()` cleanly even when callee has multiple dots.
        module_candidate = receiver
        for bare in file_imports.bare_modules:
            if module_candidate == bare or module_candidate.startswith(bare + "."):
                target = self._lookup_by_module(
                    module_candidate, method, profile, alias_map, by_file_name
                )
                if target is not None:
                    return target
                break

        # Step 2: aliased import — `import x.y as z; z.foo()`.
        head, _, rest = receiver.partition(".")
        bare_target = file_imports.aliases.get(head)
        if bare_target is not None:
            module = bare_target + (f".{rest}" if rest else "")
            return self._lookup_by_module(
                module, method, profile, alias_map, by_file_name
            )

        # Step 3: receiver is a name imported via `from MOD import NAME` —
        # e.g. `from foo import bar; bar.baz()` where bar is a submodule.
        for module, name, _level in file_imports.from_names:
            if head != name:
                continue
            sub = name + (f".{rest}" if rest else "")
            target = self._lookup_by_module(
                f"{module}.{sub}", method, profile, alias_map, by_file_name
            )
            if target is not None:
                return target

        # Step 4: receiver is a parameter of the enclosing function with a
        # known type annotation — dispatch via the type's methods. This is
        # the FastAPI Depends pattern:
        #
        #     def get_user(id: int, service: UserService = Depends(...)):
        #         return service.get(id)   # -> UserService.get
        #
        # Only the top-level head matters here (`service` in `service.foo`).
        params = params_by_artifact_id.get(enclosing.id)
        if params:
            type_name = params.get(head)
            if type_name:
                target = self._lookup_method_by_type(
                    type_name=type_name,
                    method_name=method,
                    caller_file=call_file,
                    profile=profile,
                    file_imports=file_imports,
                    alias_map=alias_map,
                    methods_by_qualifier=methods_by_qualifier,
                )
                if target is not None:
                    return target

        return None

    def _lookup_method_by_type(
        self,
        *,
        type_name: str,
        method_name: str,
        caller_file: str,
        profile: LanguageProfile,
        file_imports: "_ImportIndex",
        alias_map: dict[tuple[str, str], list[tuple[str, str]]],
        methods_by_qualifier: dict[tuple[str, str, str], CodeArtifact],
    ) -> CodeArtifact | None:
        """Find `type_name.method_name` defined somewhere reachable from
        `caller_file`. Tries: same-file class, then any from-import that
        names `type_name`, walking re-export aliases the same way named
        function calls already do.
        """
        # Same-file class.
        hit = methods_by_qualifier.get((caller_file, type_name, method_name))
        if hit is not None:
            return hit
        # Type imported via `from MOD import TYPE`.
        for module, name, _level in file_imports.from_names:
            if name != type_name:
                continue
            for real_module, real_name in self._expand(module, type_name, alias_map):
                for candidate_file in resolve_candidate_files(real_module, profile):
                    hit = methods_by_qualifier.get(
                        (candidate_file, real_name, method_name)
                    )
                    if hit is not None:
                        return hit
        return None

    def _lookup_by_module(
        self,
        module: str,
        name: str,
        profile: LanguageProfile,
        alias_map: dict[tuple[str, str], list[tuple[str, str]]],
        by_file_name: dict[tuple[str, str], CodeArtifact],
    ) -> CodeArtifact | None:
        """Try `module + name` directly, then walk re-export aliases."""
        for real_module, real_name in self._expand(module, name, alias_map):
            for candidate_file in resolve_candidate_files(real_module, profile):
                hit = by_file_name.get((candidate_file, real_name))
                if hit is not None:
                    return hit
        return None

    # ---- params index --------------------------------------------------

    def _build_params_index(
        self,
        tree: FactTree,
        repo_root: str | None,
        repo_id: str,
    ) -> dict[str, dict[str, str]]:
        """Map a function/method artifact id -> {param_name: type_hint}.

        Reads `params` data from function/method SYMBOL facts the grammar
        emits. Mirrors the same id-construction rule FunctionResolver uses
        so lookups by `enclosing.id` are direct. Methods without an
        `enclosing_class` are skipped (their artifact id can't be rebuilt).
        """
        out: dict[str, dict[str, str]] = {}
        for sym in tree.where(kind=FactKind.SYMBOL):
            sym_kind = str(sym.data.get("sym_kind", ""))
            name = str(sym.data.get("name", ""))
            if not name or sym_kind not in ("function", "method"):
                continue
            params = sym.data.get("params") or ()
            if not params:
                continue
            sym_file = _rel_to(sym.file, repo_root) if repo_root else sym.file
            if sym_kind == "function":
                artifact_id = f"fn:{repo_id}:{sym_file}:{name}"
            else:
                enclosing_class = str(sym.data.get("enclosing_class", ""))
                if not enclosing_class:
                    continue
                artifact_id = f"method:{repo_id}:{sym_file}:{enclosing_class}.{name}"
            # `params` arrives as either tuples or [name, type] lists after
            # JSON round-trips — normalize both.
            normalized: dict[str, str] = {}
            for entry in params:
                if len(entry) >= 2:
                    normalized[str(entry[0])] = str(entry[1])
            if normalized:
                out[artifact_id] = normalized
        return out

    # ---- alias map (same approach as CoverageResolver) -----------------

    def _build_alias_map(
        self,
        tree: FactTree,
        repo_root: str | None,
        languages: LanguageLibrary | None,
    ) -> dict[tuple[str, str], list[tuple[str, str]]]:
        """`(package_module, name) -> [(real_module, real_name), ...]` built
        from every aggregator file's IMPORT facts."""
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

    # ---- per-file import index -----------------------------------------

    def _file_import_index(
        self, tree: FactTree, file: str, repo_root: str | None
    ) -> "_ImportIndex":
        """Walk this file's IMPORT facts, sort by import shape."""
        bare_modules: list[str] = []
        aliases: dict[str, str] = {}
        from_names: list[tuple[str, str, int]] = []
        for fact in tree.where(kind=FactKind.IMPORT):
            fact_file = fact.file
            if repo_root is not None:
                fact_file = _rel_to(fact_file, repo_root)
            if fact_file != file:
                continue
            module = str(fact.data.get("module", ""))
            names = list(fact.data.get("names") or [])
            alias = str(fact.data.get("alias", "") or "")
            level = int(fact.data.get("level", 0) or 0)
            if names:
                # `from MOD import a, b` — track each name.
                for n in names:
                    if n:
                        from_names.append((module, n, level))
            elif alias:
                # `import MOD as ALIAS`
                aliases[alias] = module
            else:
                # `import MOD` — receiver match needs the full dotted module.
                bare_modules.append(module)
                # Allow head-of-path lookup too: `import a.b.c` → head "a".
                head = module.split(".", 1)[0]
                aliases.setdefault(head, head)
        # Sort bare modules longest-first so `a.b.c` wins over `a` when both
        # are imported in the same file.
        bare_modules.sort(key=len, reverse=True)
        return _ImportIndex(
            bare_modules=tuple(bare_modules),
            aliases=aliases,
            from_names=tuple(from_names),
        )

    # ---- profile selection ---------------------------------------------

    def _profile_for(
        self, file: str, languages: LanguageLibrary | None
    ) -> LanguageProfile:
        if languages is not None:
            profile = languages.for_file(file)
            if profile is not None:
                return profile
        return _FALLBACK_PYTHON


@dataclass(frozen=True)
class _ImportIndex:
    bare_modules: tuple[str, ...]
    """`import X` and `import X.Y` — full dotted module names."""

    aliases: dict[str, str]
    """`import X as Y` — alias → module. Also includes head-of-path aliases
    so `import a.b.c` lets `a.x()` resolve via head 'a'."""

    from_names: tuple[tuple[str, str, int], ...]
    """`from M import N` — list of (module, name, level) tuples."""


def _enclosing_artifact(
    by_file_ranges: dict[str, list[tuple[int, int, CodeArtifact]]],
    file: str,
    line: int,
) -> CodeArtifact | None:
    """Return the innermost artifact whose line range covers `line`. Innermost
    means smallest range that still contains the line, so a method nested in
    a class beats the class's own range (when classes ever land as artifacts).
    """
    ranges = by_file_ranges.get(file, ())
    best: CodeArtifact | None = None
    best_span = float("inf")
    for start, end, art in ranges:
        if start <= line <= end:
            span = end - start
            if span < best_span:
                best_span = span
                best = art
    return best


def _class_of_method(artifact: CodeArtifact) -> str:
    """Pull the class qualifier out of a method artifact's id.

    Method ids follow the convention `method:repo:file:Class.name`. Returns
    "" for non-method artifacts or any id that doesn't match that shape.
    """
    if artifact.type != "method":
        return ""
    last = artifact.id.rsplit(":", 1)[-1]
    return last.split(".", 1)[0] if "." in last else ""


def repo_id_of(artifacts: list[CodeArtifact]) -> str:
    """Pull the (single) repo id out of an artifact list.

    All artifacts in a CallResolution share a repo; this helper exists so the
    params index can rebuild artifact ids without threading repo_id through
    the public `resolve` signature.
    """
    if not artifacts:
        return ""
    return artifacts[0].repo_id


def _call_reason(call: Fact) -> str:
    callee = str(call.data.get("callee", ""))
    return f"{call.file}:{call.line} -> {callee}"


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
