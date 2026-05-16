"""Classify parsed tests into UNIT / COMPONENT / INTEGRATION / E2E.

The rule of thumb:

- UNIT        — no external imports OR every external import is mocked.
- COMPONENT   — exercises multiple in-process modules (multiple non-stdlib
                imports from the same project) but no real I/O.
- INTEGRATION — calls a real external system (HTTP, DB, queue) that isn't mocked.
- E2E         — marked with `@pytest.mark.e2e` or located under `tests/e2e/`.

Boundaries between COMPONENT and UNIT are intentionally fuzzy. When in doubt
we pick the lower tier (UNIT) — the cost of a false-negative classification
is wasted CI time, not missed tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.types import TestType
from ingestion.parsers.parser import ParsedTest

_E2E_DECORATORS = frozenset({"pytest.mark.e2e", "mark.e2e"})
_INTEGRATION_DECORATORS = frozenset({"pytest.mark.integration", "mark.integration"})


@dataclass
class Classification:
    type: TestType
    reason: str


class TestClassifier:
    def classify(self, parsed: ParsedTest) -> Classification:
        path_parts = Path(parsed.file).parts
        decorators = set(parsed.decorators)

        if decorators & _E2E_DECORATORS or _has_segment(path_parts, "tests", "e2e"):
            return Classification(TestType.E2E, "decorator or path tag")

        if decorators & _INTEGRATION_DECORATORS:
            return Classification(TestType.INTEGRATION, "@pytest.mark.integration")

        if parsed.calls_external:
            return Classification(TestType.INTEGRATION, "calls unmocked external library")

        if _has_segment(path_parts, "tests", "integration"):
            return Classification(TestType.INTEGRATION, "path tag")

        if _has_segment(path_parts, "tests", "component"):
            return Classification(TestType.COMPONENT, "path tag")

        return Classification(TestType.UNIT, "no external I/O signal")


def _has_segment(parts: tuple[str, ...], a: str, b: str) -> bool:
    """True if `a` is immediately followed by `b` somewhere in the path."""
    for i in range(len(parts) - 1):
        if parts[i] == a and parts[i + 1] == b:
            return True
    return False
