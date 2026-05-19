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
        # Walk IMPORT facts once and bucket by normalized file so per-call
        # lookups are O(1) instead of O(N_imports). Without this, every
        # CALL fact triggered a full IMPORT-fact scan, which on real repos
        # turned function_call_resolver into the dominant cost.
        self._file_imports_by_file = self._build_file_imports_by_file(tree, repo_root)
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
        repo_id = repo_id_of(artifacts_list)
        params_by_artifact_id = self._build_params_index(tree, repo_root, repo_id)
        # (file, class_name) -> {attr -> type}. Built from __init__'s
        # self_assignments + params: if `self.repo = repo` and
        # `__init__(self, repo: UserRepository)`, then `self.repo` is a
        # `UserRepository`. Catches the classic constructor-DI pattern.
        self_attr_types = self._build_self_attr_types(tree, repo_root)
        # (file, name) -> type for module-level `var = SomeClass(...)`.
        # Lets `db = Database(); db.query()` resolve to `Database.query`.
        module_var_types = self._build_module_var_types(tree, repo_root)
        # (file, alias_name) -> [dep_func_name, ...] from module-level type
        # aliases of the form `X = Annotated[T, Depends(fn)]`. Captures the
        # FastAPI Annotated DI pattern where Depends is hidden inside a
        # type subscript and thus has no enclosing-function caller.
        alias_dependencies = self._build_alias_dependencies(tree, repo_root)

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
                self_attr_types=self_attr_types,
                module_var_types=module_var_types,
            )
            if callee_artifact is None:
                # `Depends(get_db)` and similar dependency-injection markers:
                # the call's callee is `Depends`, and one of its args is a
                # `<name:get_db>` reference. Treat as a CALLS edge to the
                # named target so the chain `endpoint -> get_db` materializes.
                callee_artifact = self._resolve_dependency_injection(
                    call=call,
                    call_file=call_file,
                    profile=self._profile_for(call_file, languages),
                    file_imports=self._file_import_index(tree, call_file, repo_root),
                    alias_map=alias_map,
                    by_file_name=by_file_name,
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

        # Post-loop: emit `function -> dep_fn` edges for every function whose
        # typed parameter is an Annotated[T, Depends(fn)] alias. The Depends
        # call has no enclosing artifact (it lives at module scope inside a
        # type subscript), so the main loop above can't link it; this pass
        # restores those edges by matching param types against the alias
        # dependency map.
        if alias_dependencies:
            self._emit_alias_dependency_edges(
                artifacts_list=artifacts_list,
                alias_dependencies=alias_dependencies,
                params_by_artifact_id=params_by_artifact_id,
                by_file_name=by_file_name,
                callees=callees,
                seen_pairs=seen_pairs,
                edges=edges,
                tree=tree,
                repo_root=repo_root,
                languages=languages,
                alias_map=alias_map,
            )

        updated = [
            art.model_copy(update={"calls": tuple(callees.get(art.id, ()))})
            if callees.get(art.id)
            else art
            for art in artifacts_list
        ]
        return CallResolution(artifacts=updated, edges=edges)

    # ---- Annotated[T, Depends(fn)] alias dependency mapping -----------

    def _build_alias_dependencies(
        self,
        tree: FactTree,
        repo_root: str | None,
    ) -> dict[tuple[str, str], list[str]]:
        """`(file, alias_name) -> [dep_func_name, ...]` for type aliases of
        the form `SessionDep = Annotated[Session, Depends(get_db)]`.

        Approach: find module-level ASSIGNMENTs (the alias target) and
        correlate each with the Depends CALL facts that sit on the same
        (file, line). Both grammar emissions are already present — only
        the linking is new. Captures Annotated[T, Depends(a), Depends(b)]
        with multiple deps in one alias.
        """
        out: dict[tuple[str, str], list[str]] = {}
        # Collect (file, line) -> alias_name for every module-level assignment
        # whose RHS isn't trivially a call/name/literal — those simpler kinds
        # never hide a Depends inside a type subscript.
        alias_at: dict[tuple[str, int], str] = {}
        for assign in tree.where(kind=FactKind.ASSIGNMENT):
            if assign.data.get("scope") != "module":
                continue
            chain = list(assign.data.get("target_chain") or ())
            if len(chain) != 1:
                continue
            # Don't restrict source_kind: even `Foo = Depends(bar)` at module
            # scope (rare but legal) deserves an edge.
            alias_at[(assign.file, assign.line)] = chain[0]
        if not alias_at:
            return out
        for call in tree.where(kind=FactKind.CALL):
            callee = str(call.data.get("callee", ""))
            if callee != "Depends" and not callee.endswith(".Depends"):
                continue
            target = alias_at.get((call.file, call.line))
            if not target:
                continue
            args = call.data.get("args") or []
            if not args:
                continue
            first = args[0]
            if not isinstance(first, str) or not first.startswith("<name:"):
                continue
            dep_name = first[len("<name:") : -1]
            key_file = _rel_to(call.file, repo_root) if repo_root else call.file
            out.setdefault((key_file, target), []).append(dep_name)
        return out

    def _emit_alias_dependency_edges(
        self,
        *,
        artifacts_list: list[CodeArtifact],
        alias_dependencies: dict[tuple[str, str], list[str]],
        params_by_artifact_id: dict[str, dict[str, str]],
        by_file_name: dict[tuple[str, str], CodeArtifact],
        callees: dict[str, list[str]],
        seen_pairs: set[tuple[str, str]],
        edges: list[CallEdge],
        tree: FactTree,
        repo_root: str | None,
        languages: LanguageLibrary | None,
        alias_map: dict[tuple[str, str], list[tuple[str, str]]],
    ) -> None:
        for fn_art in artifacts_list:
            if fn_art.type not in ("function", "method"):
                continue
            params = params_by_artifact_id.get(fn_art.id)
            if not params:
                continue
            profile = self._profile_for(fn_art.file, languages)
            file_imports = self._file_import_index(tree, fn_art.file, repo_root)
            for _, param_type in params.items():
                if not param_type:
                    continue
                lookup = self._lookup_alias_deps(
                    type_name=param_type,
                    caller_file=fn_art.file,
                    profile=profile,
                    file_imports=file_imports,
                    alias_map=alias_map,
                    alias_dependencies=alias_dependencies,
                )
                if lookup is None:
                    continue
                alias_source_file, deps = lookup
                # Resolve dep names from the alias's source file — that's
                # where the dep functions are imported/defined. The caller
                # only sees the alias, never the dep names.
                dep_profile = self._profile_for(alias_source_file, languages)
                dep_imports = self._file_import_index(
                    tree, alias_source_file, repo_root
                )
                for dep_name in deps:
                    dep_art = self._resolve_named_target(
                        name=dep_name,
                        caller_file=alias_source_file,
                        profile=dep_profile,
                        file_imports=dep_imports,
                        alias_map=alias_map,
                        by_file_name=by_file_name,
                    )
                    if dep_art is None or dep_art.id == fn_art.id:
                        continue
                    pair = (fn_art.id, dep_art.id)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    callees[fn_art.id].append(dep_art.id)
                    edges.append(
                        CallEdge(
                            caller_id=fn_art.id,
                            callee_id=dep_art.id,
                            reason=f"annotated_dep via {param_type}",
                        )
                    )

    def _lookup_alias_deps(
        self,
        *,
        type_name: str,
        caller_file: str,
        profile: LanguageProfile,
        file_imports: "_ImportIndex",
        alias_map: dict[tuple[str, str], list[tuple[str, str]]],
        alias_dependencies: dict[tuple[str, str], list[str]],
    ) -> tuple[str, list[str]] | None:
        """Return `(alias_source_file, dep_names)` for `type_name` used as a
        parameter type in `caller_file`. The source file matters because
        the dep functions (e.g. `get_db`) are typically defined in or
        imported by that file — not by the caller — so a later resolution
        of the dep name must search from the alias's perspective."""
        deps = alias_dependencies.get((caller_file, type_name))
        if deps:
            return caller_file, deps
        for module, name, _level in file_imports.from_names:
            if name != type_name:
                continue
            for real_module, real_name in self._expand(module, type_name, alias_map):
                for candidate_file in resolve_candidate_files(real_module, profile):
                    found = alias_dependencies.get((candidate_file, real_name))
                    if found:
                        return candidate_file, found
        return None

    def _resolve_named_target(
        self,
        *,
        name: str,
        caller_file: str,
        profile: LanguageProfile,
        file_imports: "_ImportIndex",
        alias_map: dict[tuple[str, str], list[tuple[str, str]]],
        by_file_name: dict[tuple[str, str], CodeArtifact],
    ) -> CodeArtifact | None:
        """Resolve a bare function name (e.g. `get_db`) to its artifact.

        Same algorithm as `_resolve_dependency_injection`'s bind logic;
        factored so it's reusable both for `Depends(fn)` direct args and
        for `Annotated[…, Depends(fn)]` aliases."""
        target = by_file_name.get((caller_file, name))
        if target is not None:
            return target
        for module, imp_name, _level in file_imports.from_names:
            if imp_name != name:
                continue
            target = self._lookup_by_module(
                module, name, profile, alias_map, by_file_name
            )
            if target is not None:
                return target
        return None

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
        self_attr_types: dict[tuple[str, str], dict[str, str]],
        module_var_types: dict[tuple[str, str], str],
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

        # `self.attr.method()` — receiver_chain starts with self, attr resolves
        # through the enclosing class's __init__ params (the constructor-DI
        # pattern). The data we need is on the SYMBOL fact already; resolver
        # just reads it.
        receiver_chain = list(call.data.get("receiver_chain") or [])
        if (
            len(receiver_chain) >= 2
            and receiver_chain[0] == "self"
            and enclosing.type == "method"
        ):
            class_name = _class_of_method(enclosing)
            attr = receiver_chain[1]
            attr_type = (
                self_attr_types.get((enclosing.file, class_name), {}).get(attr)
                if class_name
                else None
            )
            if attr_type:
                target = self._lookup_method_by_type(
                    type_name=attr_type,
                    method_name=method,
                    caller_file=call_file,
                    profile=profile,
                    file_imports=file_imports,
                    alias_map=alias_map,
                    methods_by_qualifier=methods_by_qualifier,
                )
                if target is not None:
                    return target
            # Don't return None — fall through; might still match by other
            # paths (rare, but cheap to try).

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

        # Step 5: receiver is a module-level variable bound to a constructor
        # call — `db = Database(); db.query(...)`. The ASSIGNMENT fact tells
        # us the bound type; dispatch on its methods.
        module_type = module_var_types.get((call_file, head))
        if module_type:
            target = self._lookup_method_by_type(
                type_name=module_type,
                method_name=method,
                caller_file=call_file,
                profile=profile,
                file_imports=file_imports,
                alias_map=alias_map,
                methods_by_qualifier=methods_by_qualifier,
            )
            if target is not None:
                return target

        # Step 6: receiver is a module-level singleton imported from another
        # file. The dominant idiom in real FastAPI codebases:
        #
        #     # service/user_service.py (last line)
        #     user_service: UserService = UserService()
        #
        #     # api/v1/sys/user.py
        #     from backend.app.admin.service.user_service import user_service
        #     user_service.get_userinfo(...)
        #
        # We follow the import to the singleton's *source* file's ASSIGNMENT
        # fact to recover the bound type, then look up the method directly
        # against the source file's class — the caller file never imports
        # `UserService` itself, only the singleton, so the "imported type"
        # walk inside `_lookup_method_by_type` wouldn't find it.
        bound = self._resolve_imported_singleton(
            head=head,
            file_imports=file_imports,
            profile=profile,
            alias_map=alias_map,
            module_var_types=module_var_types,
        )
        if bound is not None:
            source_file, type_name = bound
            target = methods_by_qualifier.get((source_file, type_name, method))
            if target is not None:
                return target
            # The class may live in a different file than the singleton's
            # module (e.g. re-exported via __init__.py). Fall back to the
            # imports walk for that case.
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

    def _resolve_imported_singleton(
        self,
        *,
        head: str,
        file_imports: "_ImportIndex",
        profile: LanguageProfile,
        alias_map: dict[tuple[str, str], list[tuple[str, str]]],
        module_var_types: dict[tuple[str, str], str],
    ) -> tuple[str, str] | None:
        """Return (source_file, type_name) for an imported module-level singleton.

        Walks the caller file's `from MOD import NAME` imports for one whose
        local name matches `head`. Then expands re-export aliases (handles
        `__init__.py` re-exports) and probes the candidate source files for
        a module-level ASSIGNMENT named `head` whose type was recorded by
        `_build_module_var_types`. Returns the (file, type) pair, or None.
        """
        for module, name, _level in file_imports.from_names:
            if name != head:
                continue
            for real_module, real_name in self._expand(module, head, alias_map):
                for candidate_file in resolve_candidate_files(real_module, profile):
                    t = module_var_types.get((candidate_file, real_name))
                    if t:
                        return candidate_file, t
        return None

    def _resolve_dependency_injection(
        self,
        *,
        call: Fact,
        call_file: str,
        profile: LanguageProfile,
        file_imports: "_ImportIndex",
        alias_map: dict[tuple[str, str], list[tuple[str, str]]],
        by_file_name: dict[tuple[str, str], CodeArtifact],
    ) -> CodeArtifact | None:
        """Recognize FastAPI's `Depends(get_db)` / `Depends(get_user)` shape.

        These calls are passing-a-function semantically: the function `X` is
        guaranteed to be invoked by the framework. Without recognizing this,
        the call graph loses every `endpoint -> dependency_provider` edge
        — exactly the user-reported "get_db is isolated" symptom.

        Heuristic: if the call's callee is `Depends` (or ends with `.Depends`)
        and the first arg is a `<name:X>` placeholder, resolve X like a bare
        named call. We deliberately don't recognize `Annotated[..., Depends(X)]`
        yet — the args parser flattens that — but the dominant pattern is
        the bare `Depends(X)` form, which works.
        """
        callee = str(call.data.get("callee", ""))
        if callee != "Depends" and not callee.endswith(".Depends"):
            return None
        args = call.data.get("args") or []
        if not args:
            return None
        first = args[0]
        if not isinstance(first, str) or not first.startswith("<name:"):
            return None
        dep_name = first[len("<name:") : -1]
        # Same-file def?
        target = by_file_name.get((call_file, dep_name))
        if target is not None:
            return target
        # Imported by name from somewhere — reuse the from-import path.
        for module, name, _level in file_imports.from_names:
            if name != dep_name:
                continue
            target = self._lookup_by_module(
                module, dep_name, profile, alias_map, by_file_name
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

    # ---- self.attr type inference -------------------------------------

    def _build_self_attr_types(
        self,
        tree: FactTree,
        repo_root: str | None,
    ) -> dict[tuple[str, str], dict[str, str]]:
        """`(file, class_name) -> {attr -> type_hint}` derived from each
        `__init__`'s params + self_assignments. Catches the constructor-DI
        pattern: `def __init__(self, repo: Repo): self.repo = repo` yields
        `self.repo -> Repo`. Same idea for `self.x = Foo()` (literal type)
        and `self.y: T = ...` (annotated).
        """
        out: dict[tuple[str, str], dict[str, str]] = {}
        for sym in tree.where(kind=FactKind.SYMBOL):
            if sym.data.get("sym_kind") != "method":
                continue
            if sym.data.get("name") != "__init__":
                continue
            class_name = str(sym.data.get("enclosing_class", ""))
            if not class_name:
                continue
            file = _rel_to(sym.file, repo_root) if repo_root else sym.file
            # param-name -> type
            param_types: dict[str, str] = {}
            for entry in sym.data.get("params") or ():
                if len(entry) >= 2 and entry[1]:
                    param_types[str(entry[0])] = str(entry[1])
            attr_types: dict[str, str] = {}
            for sa in sym.data.get("self_assignments") or ():
                attr = str(sa.get("attr", ""))
                if not attr:
                    continue
                # Annotated assignment wins (`self.x: T = ...`).
                hint = str(sa.get("type_hint", ""))
                if hint:
                    attr_types[attr] = hint
                    continue
                source_kind = str(sa.get("source_kind", ""))
                source = str(sa.get("source", ""))
                if source_kind == "name" and source in param_types:
                    attr_types[attr] = param_types[source]
                elif source_kind == "call" and source:
                    # `self.db = Database()` -> Database
                    attr_types[attr] = source
            if attr_types:
                out[(file, class_name)] = attr_types
        return out

    # ---- module-level variable type inference -------------------------

    def _build_module_var_types(
        self,
        tree: FactTree,
        repo_root: str | None,
    ) -> dict[tuple[str, str], str]:
        """`(file, var_name) -> type_hint` for module-level `var = SomeClass(...)`
        or `var: T = ...`. Lets calls on module singletons resolve."""
        out: dict[tuple[str, str], str] = {}
        for fact in tree.where(kind=FactKind.ASSIGNMENT):
            if fact.data.get("scope") != "module":
                continue
            chain = list(fact.data.get("target_chain") or ())
            if len(chain) != 1:
                continue
            var = chain[0]
            type_hint = str(fact.data.get("type_hint", ""))
            if type_hint:
                out[(fact.file if repo_root is None else _rel_to(fact.file, repo_root), var)] = type_hint
                continue
            source_kind = str(fact.data.get("source_kind", ""))
            source = str(fact.data.get("source", ""))
            if source_kind == "call" and source:
                out[
                    (fact.file if repo_root is None else _rel_to(fact.file, repo_root), var)
                ] = source
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
        """O(1) lookup into the precomputed per-file index built at the
        start of `resolve()`. `tree` and `repo_root` are kept on the
        signature for backwards compatibility with callers that still
        pass them — they're ignored here."""
        cache = getattr(self, "_file_imports_by_file", None)
        if cache is None:
            # Defensive: someone called this outside a `resolve()` scope.
            # Fall back to building on demand (slow path, used by tests).
            return self._build_file_imports_by_file(tree, repo_root).get(
                file, _EMPTY_IMPORT_INDEX
            )
        return cache.get(file, _EMPTY_IMPORT_INDEX)

    def _build_file_imports_by_file(
        self, tree: FactTree, repo_root: str | None
    ) -> dict[str, "_ImportIndex"]:
        """One pass over IMPORT facts, bucketed by normalized file."""
        bare: dict[str, list[str]] = {}
        aliases: dict[str, dict[str, str]] = {}
        from_names: dict[str, list[tuple[str, str, int]]] = {}
        for fact in tree.where(kind=FactKind.IMPORT):
            fact_file = fact.file
            if repo_root is not None:
                fact_file = _rel_to(fact_file, repo_root)
            module = str(fact.data.get("module", ""))
            names = list(fact.data.get("names") or [])
            alias = str(fact.data.get("alias", "") or "")
            level = int(fact.data.get("level", 0) or 0)
            if names:
                bucket = from_names.setdefault(fact_file, [])
                for n in names:
                    if n:
                        bucket.append((module, n, level))
            elif alias:
                aliases.setdefault(fact_file, {})[alias] = module
            else:
                bare.setdefault(fact_file, []).append(module)
                head = module.split(".", 1)[0]
                aliases.setdefault(fact_file, {}).setdefault(head, head)
        out: dict[str, _ImportIndex] = {}
        all_files = set(bare) | set(aliases) | set(from_names)
        for f in all_files:
            bm = bare.get(f, [])
            bm.sort(key=len, reverse=True)
            out[f] = _ImportIndex(
                bare_modules=tuple(bm),
                aliases=aliases.get(f, {}),
                from_names=tuple(from_names.get(f, ())),
            )
        return out

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


_EMPTY_IMPORT_INDEX = _ImportIndex(bare_modules=(), aliases={}, from_names=())


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
