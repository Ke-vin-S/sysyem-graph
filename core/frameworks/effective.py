"""EffectiveFramework — stock + per-repo overlay merged.

Resolvers consume EffectiveFramework, not raw FrameworkDefinition, so they
automatically pick up any LLM-learned overlay without conditional logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.frameworks.definition import (
    FrameworkDefinition,
    HttpClientPatterns,
    MockPatterns,
    TestPatterns,
)
from core.frameworks.overlay import RepoOverlay


@dataclass(frozen=True)
class EffectiveFramework:
    """Merged view used at query time. Same shape as FrameworkDefinition but
    with the overlay's additions folded in (and `internal_test_wrappers`
    removed from external_modules)."""

    name: str
    language: str
    routes: object  # RoutePatterns | None  (kept as-is from stock)
    tests: TestPatterns | None
    mocks: MockPatterns | None
    http_clients: HttpClientPatterns | None


def compose(definition: FrameworkDefinition, overlay: RepoOverlay | None) -> EffectiveFramework:
    if overlay is None:
        return EffectiveFramework(
            name=definition.name,
            language=definition.language,
            routes=definition.routes,
            tests=definition.tests,
            mocks=definition.mocks,
            http_clients=definition.http_clients,
        )

    tests = definition.tests
    if tests is not None and overlay.test_annotations:
        tests = tests.model_copy(
            update={
                "decorator_callees": tuple(
                    dict.fromkeys((*tests.decorator_callees, *overlay.test_annotations))
                )
            }
        )

    mocks = definition.mocks
    if mocks is not None and overlay.mock_annotations:
        mocks = mocks.model_copy(
            update={
                "field_annotations": tuple(
                    dict.fromkeys((*mocks.field_annotations, *overlay.mock_annotations))
                )
            }
        )

    http_clients = definition.http_clients
    if http_clients is not None:
        merged_modules = tuple(
            dict.fromkeys((*http_clients.external_modules, *overlay.external_modules))
        )
        if overlay.internal_test_wrappers:
            merged_modules = tuple(
                m for m in merged_modules if m not in set(overlay.internal_test_wrappers)
            )
        http_clients = http_clients.model_copy(update={"external_modules": merged_modules})

    return EffectiveFramework(
        name=definition.name,
        language=definition.language,
        routes=definition.routes,
        tests=tests,
        mocks=mocks,
        http_clients=http_clients,
    )
