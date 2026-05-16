"""TestParserAdapter: walk local repos, parse test files, emit TestCase records."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from core.adapters.base import AdapterResult, Coverage, IngestionAdapter, IngestionContext
from core.types import LineRange, TestCase
from core.types.errors import IngestionError
from ingestion.adapters.testparser.classifier import TestClassifier
from ingestion.adapters.testparser.config import TestParserAdapterConfig
from ingestion.adapters.testparser.coverage import CoverageEstimator
from ingestion.parsers import JavaParser, Parser, PythonParser
from ingestion.parsers.parser import ParsedTest

logger = logging.getLogger(__name__)


class TestParserAdapter(IngestionAdapter):
    """Walks `config.root`, parses tests, classifies them, emits TestCase records.

    Priority 70 — runs after Datadog/GitHub. It depends on a checked-out
    filesystem snapshot of the repos, not on remote API access.
    """

    name = "testparser"
    priority = 70

    def __init__(
        self,
        config: TestParserAdapterConfig,
        *,
        parsers: Iterable[Parser] | None = None,
        classifier: TestClassifier | None = None,
        coverage_estimator: CoverageEstimator | None = None,
    ) -> None:
        self._config = config
        self._parsers = tuple(parsers) if parsers else (PythonParser(), JavaParser())
        self._classifier = classifier or TestClassifier()
        self._coverage = coverage_estimator or CoverageEstimator(module_to_repo={})
        self._suffixes = tuple({suffix for parser in self._parsers for suffix in parser.suffixes})

    def extract(self, context: IngestionContext) -> AdapterResult:
        result = AdapterResult(adapter=self.name)
        root = self._config.root
        if not root.exists():
            raise IngestionError("testparser", f"root path does not exist: {root}")

        repo_dirs = self._discover_repos(root, context.repos)
        scanned = 0
        for repo_dir in repo_dirs:
            repo_id = repo_dir.name
            for file in _iter_test_files(repo_dir, self._config.excluded_dirs, self._suffixes):
                parser = self._parser_for(file)
                if parser is None:
                    continue
                try:
                    content = file.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    result.warnings.append(f"{file}: {exc}")
                    continue
                for parsed in parser.parse(file, content):
                    result.tests.append(self._to_test_case(parsed, repo_id=repo_id, file=file, root=repo_dir))
            scanned += 1

        result.coverage = Coverage(
            services_scanned=scanned,
            services_total=len(repo_dirs) or None,
            notes=f"root={root}",
        )
        return result

    def _discover_repos(self, root: Path, repos_filter: tuple[str, ...]) -> list[Path]:
        if not root.is_dir():
            return [root]
        candidates = [p for p in sorted(root.iterdir()) if p.is_dir() and p.name not in self._config.excluded_dirs]
        if repos_filter:
            allowed = set(repos_filter)
            candidates = [p for p in candidates if p.name in allowed]
        return candidates or [root]

    def _parser_for(self, file: Path) -> Parser | None:
        for parser in self._parsers:
            if parser.matches(file):
                return parser
        return None

    def _to_test_case(self, parsed: ParsedTest, *, repo_id: str, file: Path, root: Path) -> TestCase:
        classification = self._classifier.classify(parsed)
        affected = self._coverage.estimate(parsed, own_repo=repo_id)
        rel_path = file.relative_to(root) if file.is_relative_to(root) else file
        return TestCase(
            id=f"test:{repo_id}:{rel_path}:{parsed.name}",
            repoId=repo_id,
            type=classification.type,
            name=parsed.name,
            file=str(rel_path),
            lineRange=LineRange(start=parsed.line_start, end=parsed.line_end),
            duration_ms=0,
            flakiness_score=0.0,
            priority="HIGH" if classification.type.value == "INTEGRATION" else "MEDIUM",
            affectedRepos=affected,
        )


def _iter_test_files(
    root: Path, excluded: tuple[str, ...], suffixes: tuple[str, ...]
) -> Iterable[Path]:
    excluded_set = set(excluded)
    for suffix in suffixes:
        for path in root.rglob(f"*{suffix}"):
            if any(part in excluded_set for part in path.parts):
                continue
            if _looks_like_test_file(path):
                yield path


def _looks_like_test_file(path: Path) -> bool:
    name = path.name
    parts = path.parts
    if "tests" in parts or "test" in parts:
        return True
    suffix = path.suffix
    if suffix == ".py":
        return name.startswith("test_") or name.endswith("_test.py")
    if suffix == ".java":
        # JUnit/Maven conventions: `FooTest.java`, `FooTests.java`, `FooIT.java`,
        # or anything under `src/test/java`. We also catch `Test*.java` for
        # codebases that prefix.
        if "java" in parts and "test" in parts:
            return True
        stem = name[: -len(".java")]
        return (
            stem.endswith("Test")
            or stem.endswith("Tests")
            or stem.endswith("IT")
            or stem.startswith("Test")
        )
    return False
