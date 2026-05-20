"""TestParserAdapter — walks local repos and emits a full graph slice per repo.

What "full graph slice" means:

  * one Service node per repo                       (so Neo4j has a root node)
  * CodeArtifact(type=endpoint) per HTTP route      (from EndpointResolver)
  * CodeArtifact(type=function|class|method) per
    top-level named code surface                    (from FunctionResolver)
  * TestCase per test function                      (from TestResolver)
  * TestCase.covers_artifacts populated by linking
    test-file imports back to artifacts             (CoverageResolver)

The output is intentionally Neo4j-ready: every node has a stable ID, every
relationship is encodable from foreign keys on the records, and
TestCase.covers_artifacts gives the explicit (TestCase)-[:COVERS]->
(CodeArtifact) edges.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.adapters.base import AdapterResult, Coverage, IngestionAdapter, IngestionContext
from core.facts import FactKind, FactTree
from core.frameworks import compose, detect_frameworks, load_library
from core.frameworks.library import DEFAULT_FRAMEWORKS_DIR, FrameworkLibrary
from core.resolvers import (
    ConfigBindingResolver,
    CoverageResolver,
    DataModelResolver,
    EndpointResolver,
    FunctionCallResolver,
    FunctionResolver,
    KafkaResolver,
    MockResolver,
    QueryResolver,
    ResolverContext,
    TestResolver,
)
from core.config import OracleStackSettings
from core.resolvers.forms_service_resolver import extract_forms_services
from core.types import CodeArtifact, Endpoint, LineRange, Service, TestCase
from core.types.errors import IngestionError
from core.walker import Walker
from ingestion.adapters.testparser.config import TestParserAdapterConfig

logger = logging.getLogger(__name__)


@contextmanager
def _timed(stage: str, repo_id: str):
    """Log entry + exit for a pipeline stage. Big repos otherwise look hung.

    Pattern: every resolver gets wrapped so the operator sees, in order,
    "starting <stage>" and "<stage>: <n_items> in <s>s" with the stage
    name as the searchable key.
    """
    started = time.monotonic()
    logger.info("testparser[%s]: %s starting", repo_id, stage)
    yield
    logger.info(
        "testparser[%s]: %s done in %.2fs",
        repo_id, stage, time.monotonic() - started,
    )


class TestParserAdapter(IngestionAdapter):
    """Walks `config.root`, builds a FactTree per repo, resolves the full slice."""

    name = "testparser"
    priority = 70

    def __init__(
        self,
        config: TestParserAdapterConfig,
        *,
        walker: Walker | None = None,
        test_resolver: TestResolver | None = None,
        function_resolver: FunctionResolver | None = None,
        endpoint_resolver: EndpointResolver | None = None,
        function_call_resolver: FunctionCallResolver | None = None,
        mock_resolver: MockResolver | None = None,
        data_model_resolver: DataModelResolver | None = None,
        query_resolver: QueryResolver | None = None,
        kafka_resolver: KafkaResolver | None = None,
        config_binding_resolver: ConfigBindingResolver | None = None,
        coverage_resolver: CoverageResolver | None = None,
        library: FrameworkLibrary | None = None,
        languages: object | None = None,
    ) -> None:
        from core.languages import load_library as _load_languages

        self._config = config
        self._walker = walker or Walker()
        self._test_resolver = test_resolver or TestResolver()
        self._function_resolver = function_resolver or FunctionResolver()
        self._endpoint_resolver = endpoint_resolver or EndpointResolver()
        self._function_call_resolver = function_call_resolver or FunctionCallResolver()
        self._mock_resolver = mock_resolver or MockResolver()
        self._data_model_resolver = data_model_resolver or DataModelResolver()
        self._query_resolver = query_resolver or QueryResolver()
        self._kafka_resolver = kafka_resolver or KafkaResolver()
        self._config_binding_resolver = (
            config_binding_resolver or ConfigBindingResolver()
        )
        self._coverage_resolver = coverage_resolver or CoverageResolver()
        self._library = library or load_library(DEFAULT_FRAMEWORKS_DIR)
        # Lazy default: load core/languages/<lang>/profile.yaml once;
        # tests/temp dirs without the directory fall through with an empty
        # library and the resolver uses its Python fallback.
        if languages is None:
            try:
                languages = _load_languages()
            except Exception:
                from core.languages import LanguageLibrary

                languages = LanguageLibrary()
        self._languages = languages

    def extract(self, context: IngestionContext) -> AdapterResult:
        result = AdapterResult(adapter=self.name)
        root = self._config.root
        if not root.exists():
            raise IngestionError("testparser", f"root path does not exist: {root}")

        repo_dirs = self._discover_repos(root, context.repos)
        scanned = 0
        for repo_dir in repo_dirs:
            self._extract_repo(repo_dir, context, result)
            scanned += 1

        result.coverage = Coverage(
            services_scanned=scanned,
            services_total=len(repo_dirs) or None,
            notes=f"root={root}",
        )
        return result

    def _extract_repo(
        self, repo_dir: Path, context: IngestionContext, result: AdapterResult
    ) -> None:
        repo_id = repo_dir.name
        repo_root_abs = str(repo_dir.resolve())
        repo_started = time.monotonic()
        logger.info("testparser[%s]: repo extraction starting at %s", repo_id, repo_dir)

        with _timed("walk + parse", repo_id):
            tree = self._walker.walk(repo_dir, repo_id=repo_id)

        with _timed("framework detect", repo_id):
            detected = detect_frameworks(tree, self._library)
            effective = tuple(compose(fw, None) for fw in detected)
            logger.info(
                "testparser[%s]: detected frameworks=%s",
                repo_id, sorted({fw.name for fw in detected}),
            )
        ctx = ResolverContext(
            tree=tree, frameworks=effective, repo_id=repo_id, languages=self._languages
        )

        # 1. Service node for this repo. Language is the majority extension.
        service = self._build_service(repo_dir, tree, detected, context.now)
        result.services.append(service)

        # 1b. Oracle Forms services — additional Service per `.fmb`/`.fmx`
        # file detected in this repo, plus any names listed in
        # `ORACLE_FORMS_APPS`. Each becomes a node with language='oracle_forms'.
        forms_extras = OracleStackSettings().forms_apps
        result.services.extend(
            extract_forms_services(
                tree, repo_id=repo_id, extras=forms_extras, now=context.now,
            )
        )

        # 2. Code structure artifacts (functions/classes/methods) — keyed by
        #    paths relative to the repo root so they match coverage lookups.
        with _timed("function_resolver", repo_id):
            function_artifacts = [
                self._rebase(artifact, repo_root_abs)
                for artifact in self._function_resolver.resolve(ctx)
            ]
            logger.info(
                "testparser[%s]: function_resolver -> %d artifacts",
                repo_id, len(function_artifacts),
            )

        # 2b. Resolve function→function calls. The resolver returns updated
        #     artifacts with `calls` populated; we feed those forward.
        with _timed("function_call_resolver", repo_id):
            call_resolution = self._function_call_resolver.resolve(
                tree=tree,
                artifacts=function_artifacts,
                repo_root=repo_root_abs,
                languages=self._languages,
            )
            function_artifacts = call_resolution.artifacts
            logger.info(
                "testparser[%s]: function_call_resolver -> %d CALLS edges",
                repo_id, len(call_resolution.edges),
            )
        result.artifacts.extend(function_artifacts)

        # 3. Endpoint records — now a first-class node type. We look up the
        #    handler function artifact by (file, name) so the loader can wire
        #    the (Endpoint)-[:HANDLED_BY]->(CodeArtifact) edge.
        with _timed("endpoint_resolver", repo_id):
            handler_index = {(a.file, a.name): a.id for a in function_artifacts}
            endpoints: list[Endpoint] = []
            for endpoint in self._endpoint_resolver.resolve(ctx):
                handler_rel = _make_relative(endpoint.handler_file, repo_root_abs)
                handler_artifact_id = handler_index.get((handler_rel, endpoint.handler_symbol))
                endpoints.append(
                    Endpoint(
                        id=f"endpoint:{repo_id}:{endpoint.method}:{endpoint.full_path}",
                        repoId=repo_id,
                        method=endpoint.method,
                        path=endpoint.full_path,
                        framework=endpoint.framework or "unknown",
                        handlerArtifactId=handler_artifact_id,
                        handlerFile=handler_rel,
                        handlerSymbol=endpoint.handler_symbol,
                        isPublic=True,
                        producedBy="endpoint_resolver",
                        fromFacts=endpoint.derivation,
                    )
                )
            logger.info(
                "testparser[%s]: endpoint_resolver -> %d endpoints",
                repo_id, len(endpoints),
            )
        result.endpoints.extend(endpoints)

        # 4. Tests.
        with _timed("test_resolver", repo_id):
            tests = self._test_resolver.resolve(ctx)
            # Rewrite test file paths to repo-relative for stable IDs across runs.
            tests = [self._rebase_test(t, repo_root_abs, repo_id) for t in tests]
            logger.info(
                "testparser[%s]: test_resolver -> %d tests",
                repo_id, len(tests),
            )

        # 5. Coverage edges — link tests to function artifacts via the test
        #    file's imports. Endpoints are excluded by design: tests import
        #    handler functions by name, not URL paths.
        with _timed("coverage_resolver", repo_id):
            coverage = self._coverage_resolver.resolve(
                tree=tree,
                tests=tests,
                artifacts=function_artifacts,
                repo_root=repo_root_abs,
                languages=self._languages,
            )
            logger.info(
                "testparser[%s]: coverage_resolver -> %d COVERS edges",
                repo_id, len(coverage.edges),
            )
        result.tests.extend(coverage.tests)

        # 6. Mocks — turn @patch / @patch.object decorators into Mock records,
        #    resolving target strings against the function artifact index.
        with _timed("mock_resolver", repo_id):
            mock_resolution = self._mock_resolver.resolve(
                tree=tree,
                tests=coverage.tests,
                artifacts=function_artifacts,
                frameworks=effective,
                repo_id=repo_id,
                repo_root=repo_root_abs,
                languages=self._languages,
            )
            logger.info(
                "testparser[%s]: mock_resolver -> %d mocks",
                repo_id, len(mock_resolution.mocks),
            )
        result.mocks.extend(mock_resolution.mocks)

        # 7. Data models — pydantic / sqlalchemy / dataclass classes, driven
        #    by per-framework data_models YAML patterns.
        with _timed("data_model_resolver", repo_id):
            dm_resolution = self._data_model_resolver.resolve(
                tree=tree, frameworks=effective, repo_id=repo_id, repo_root=repo_root_abs,
            )
            logger.info(
                "testparser[%s]: data_model_resolver -> %d data_models",
                repo_id, len(dm_resolution.data_models),
            )
        result.data_models.extend(dm_resolution.data_models)

        # 8. Queries — raw SQL + ORM call sites attributed to their
        #    enclosing function for EXECUTES edges.
        with _timed("query_resolver", repo_id):
            query_resolution = self._query_resolver.resolve(
                tree=tree, artifacts=function_artifacts,
                frameworks=effective, repo_id=repo_id, repo_root=repo_root_abs,
            )
            logger.info(
                "testparser[%s]: query_resolver -> %d queries",
                repo_id, len(query_resolution.queries),
            )
        result.queries.extend(query_resolution.queries)

        # 9. Kafka — producer/consumer call sites; topics are global join
        #    keys that stitch cross-repo PRODUCES/CONSUMES edges.
        with _timed("kafka_resolver", repo_id):
            kafka_resolution = self._kafka_resolver.resolve(
                tree=tree, artifacts=function_artifacts,
                frameworks=effective, repo_id=repo_id, repo_root=repo_root_abs,
            )
            logger.info(
                "testparser[%s]: kafka_resolver -> %d topics, %d producers, %d consumers",
                repo_id, len(kafka_resolution.topics),
                len(kafka_resolution.producers), len(kafka_resolution.consumers),
            )
        result.kafka_topics.extend(kafka_resolution.topics)
        result.kafka_producers.extend(kafka_resolution.producers)
        result.kafka_consumers.extend(kafka_resolution.consumers)

        # 10. Config bindings — turn CONFIG_VALUE facts that look like
        #     URLs/hostnames into ExternalConnection records. Cheaper
        #     deterministic counterpart to Datadog.
        with _timed("config_binding_resolver", repo_id):
            cfg = self._config_binding_resolver.resolve(
                tree=tree, repo_id=repo_id, source_service_id=service.id, now=context.now,
            )
            logger.info(
                "testparser[%s]: config_binding_resolver -> %d connections",
                repo_id, len(cfg.connections),
            )
        result.connections.extend(cfg.connections)

        logger.info(
            "testparser[%s]: repo extraction done in %.2fs",
            repo_id, time.monotonic() - repo_started,
        )

    def _build_service(
        self,
        repo_dir: Path,
        tree: FactTree,
        detected_frameworks,
        now: datetime,
    ) -> Service:
        language = self._infer_language(tree)
        framework_names = ",".join(sorted({fw.name for fw in detected_frameworks})) or "unknown"
        return Service(
            id=repo_dir.name,
            name=repo_dir.name,
            repoUrl=f"file://{repo_dir.resolve()}",
            language=language,
            framework=framework_names,
            owner="unknown",
            createdAt=now,
            lastUpdatedAt=now,
            isActive=True,
            producedBy="testparser_adapter",
        )

    def _infer_language(self, tree: FactTree) -> str:
        """Pick the language name whose extensions account for the most
        SYMBOL facts in the repo. Falls back to "unknown" if the language
        library is empty (which happens in test environments without
        languages/ on disk)."""
        suffix_counts: Counter[str] = Counter()
        for fact in tree.where(kind=FactKind.SYMBOL):
            suffix = Path(fact.file).suffix.lower()
            if suffix:
                suffix_counts[suffix] += 1
        if not suffix_counts:
            return "unknown"
        for most_common_suffix, _ in suffix_counts.most_common():
            profile = self._languages.for_extension(most_common_suffix)
            if profile is not None:
                return profile.name
        return "unknown"

    def _rebase(self, artifact: CodeArtifact, repo_root_abs: str) -> CodeArtifact:
        """Rewrite an artifact's `file` to repo-relative and rebuild its ID."""
        rel = _make_relative(artifact.file, repo_root_abs)
        if rel == artifact.file:
            return artifact
        # Rebuild ID with the rel path so it matches what callers will look up.
        prefix = artifact.id.split(":", 2)[0]  # 'fn', 'class', 'method', 'endpoint'
        new_id = f"{prefix}:{artifact.repo_id}:{rel}:{artifact.name}"
        if prefix == "method":
            # Method IDs are `method:repo:file:Class.name` — preserve the qualifier.
            qualifier_suffix = artifact.id.rsplit(":", 1)[-1]
            new_id = f"method:{artifact.repo_id}:{rel}:{qualifier_suffix}"
        return artifact.model_copy(update={"file": rel, "id": new_id})

    def _rebase_test(self, test: TestCase, repo_root_abs: str, repo_id: str) -> TestCase:
        rel = _make_relative(test.file, repo_root_abs)
        if rel == test.file:
            return test
        new_id = f"test:{repo_id}:{rel}:{test.name}"
        return test.model_copy(update={"file": rel, "id": new_id})

    def _discover_repos(self, root: Path, repos_filter: tuple[str, ...]) -> list[Path]:
        if not root.is_dir():
            return [root]

        # Single-repo mode: the root itself IS the service. Either the
        # operator forced it via TESTPARSER_SINGLE_REPO=true, or auto-
        # detection found repo-shaped markers at the root.
        single_repo = self._config.single_repo
        if single_repo is None:
            single_repo = _looks_like_repo(root)
        if single_repo:
            if repos_filter and root.name not in set(repos_filter):
                return []
            return [root]

        # Parent-of-repos mode: each subdirectory is its own service.
        candidates = [
            p
            for p in sorted(root.iterdir())
            if p.is_dir() and p.name not in self._config.excluded_dirs
        ]
        if repos_filter:
            allowed = set(repos_filter)
            candidates = [p for p in candidates if p.name in allowed]
        return candidates or [root]


# Files / directories that, when present at a path's root, strongly suggest
# that path IS a single repository (rather than a parent directory holding
# several repos). The set is conservative — we'd rather miss the auto-detect
# and have the operator pass TESTPARSER_SINGLE_REPO=true than falsely treat
# a monorepo of services as one mega-service.
_REPO_MARKERS = (
    ".git",            # most reliable signal; works for both dirs and worktree files
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
)


def _looks_like_repo(path: Path) -> bool:
    """True when `path` itself looks like a repo root."""
    for marker in _REPO_MARKERS:
        if (path / marker).exists():
            return True
    return False


def _make_relative(file: str, repo_root_abs: str) -> str:
    """Rebase an absolute path to a repo-relative POSIX string.

    Always returns forward-slash form so the value compares cleanly against
    the `_rel_to` POSIX-normalized paths the resolvers produce. Without
    this, Windows runs return `src\\billing\\store.py` from the adapter
    while resolvers produce `src/billing/store.py` — every enclosing
    lookup misses and Kafka/Query/DataModel emit zero records.
    """
    if not repo_root_abs or not file:
        return file
    try:
        return Path(file).resolve().relative_to(repo_root_abs).as_posix()
    except ValueError:
        return file
