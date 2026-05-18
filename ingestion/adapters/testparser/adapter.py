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
from collections import Counter
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
from core.types import CodeArtifact, Endpoint, LineRange, Service, TestCase
from core.types.errors import IngestionError
from core.walker import Walker
from ingestion.adapters.testparser.config import TestParserAdapterConfig

logger = logging.getLogger(__name__)


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
        tree = self._walker.walk(repo_dir, repo_id=repo_id)
        detected = detect_frameworks(tree, self._library)
        effective = tuple(compose(fw, None) for fw in detected)
        ctx = ResolverContext(
            tree=tree, frameworks=effective, repo_id=repo_id, languages=self._languages
        )

        # 1. Service node for this repo. Language is the majority extension.
        service = self._build_service(repo_dir, tree, detected, context.now)
        result.services.append(service)

        # 2. Code structure artifacts (functions/classes/methods) — keyed by
        #    paths relative to the repo root so they match coverage lookups.
        function_artifacts = [
            self._rebase(artifact, repo_root_abs) for artifact in self._function_resolver.resolve(ctx)
        ]

        # 2b. Resolve function→function calls. The resolver returns updated
        #     artifacts with `calls` populated; we feed those forward.
        call_resolution = self._function_call_resolver.resolve(
            tree=tree,
            artifacts=function_artifacts,
            repo_root=repo_root_abs,
            languages=self._languages,
        )
        function_artifacts = call_resolution.artifacts
        result.artifacts.extend(function_artifacts)

        # 3. Endpoint records — now a first-class node type. We look up the
        #    handler function artifact by (file, name) so the loader can wire
        #    the (Endpoint)-[:HANDLED_BY]->(CodeArtifact) edge.
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
                )
            )
        result.endpoints.extend(endpoints)

        # 4. Tests.
        tests = self._test_resolver.resolve(ctx)
        # Rewrite test file paths to repo-relative for stable IDs across runs.
        tests = [self._rebase_test(t, repo_root_abs, repo_id) for t in tests]

        # 5. Coverage edges — link tests to function artifacts via the test
        #    file's imports. Endpoints are excluded by design: tests import
        #    handler functions by name, not URL paths.
        coverage = self._coverage_resolver.resolve(
            tree=tree,
            tests=tests,
            artifacts=function_artifacts,
            repo_root=repo_root_abs,
            languages=self._languages,
        )
        result.tests.extend(coverage.tests)

        # 6. Mocks — turn @patch / @patch.object decorators into Mock records,
        #    resolving target strings against the function artifact index.
        mock_resolution = self._mock_resolver.resolve(
            tree=tree,
            tests=coverage.tests,
            artifacts=function_artifacts,
            frameworks=effective,
            repo_id=repo_id,
            repo_root=repo_root_abs,
            languages=self._languages,
        )
        result.mocks.extend(mock_resolution.mocks)

        # 7. Data models — pydantic / sqlalchemy / dataclass classes, driven
        #    by per-framework data_models YAML patterns.
        dm_resolution = self._data_model_resolver.resolve(
            tree=tree, frameworks=effective, repo_id=repo_id, repo_root=repo_root_abs,
        )
        result.data_models.extend(dm_resolution.data_models)

        # 8. Queries — raw SQL + ORM call sites attributed to their
        #    enclosing function for EXECUTES edges.
        query_resolution = self._query_resolver.resolve(
            tree=tree, artifacts=function_artifacts,
            frameworks=effective, repo_id=repo_id, repo_root=repo_root_abs,
        )
        result.queries.extend(query_resolution.queries)

        # 9. Kafka — producer/consumer call sites; topics are global join
        #    keys that stitch cross-repo PRODUCES/CONSUMES edges.
        kafka_resolution = self._kafka_resolver.resolve(
            tree=tree, artifacts=function_artifacts,
            frameworks=effective, repo_id=repo_id, repo_root=repo_root_abs,
        )
        result.kafka_topics.extend(kafka_resolution.topics)
        result.kafka_producers.extend(kafka_resolution.producers)
        result.kafka_consumers.extend(kafka_resolution.consumers)

        # 10. Config bindings — turn CONFIG_VALUE facts that look like
        #     URLs/hostnames into ExternalConnection records. Cheaper
        #     deterministic counterpart to Datadog.
        cfg = self._config_binding_resolver.resolve(
            tree=tree, repo_id=repo_id, source_service_id=service.id, now=context.now,
        )
        result.connections.extend(cfg.connections)

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
        candidates = [
            p
            for p in sorted(root.iterdir())
            if p.is_dir() and p.name not in self._config.excluded_dirs
        ]
        if repos_filter:
            allowed = set(repos_filter)
            candidates = [p for p in candidates if p.name in allowed]
        return candidates or [root]


def _make_relative(file: str, repo_root_abs: str) -> str:
    if not repo_root_abs or not file:
        return file
    try:
        return str(Path(file).resolve().relative_to(repo_root_abs))
    except ValueError:
        return file
