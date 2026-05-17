"""Framework knowledge as data, not code.

This package loads `frameworks/*.yaml` files at the repo root into validated
`FrameworkDefinition` Pydantic models. Grammars and resolvers consume those
definitions to decide what counts as a test, a mock, an external call, or a
route — no hardcoded annotation lists in any parser.
"""

from core.frameworks.definition import (
    DetectorRule,
    FrameworkDefinition,
    HttpClientPatterns,
    MockPatterns,
    RouteMountCall,
    RoutePatterns,
    TestPatterns,
)
from core.frameworks.detector import detect_frameworks
from core.frameworks.effective import EffectiveFramework, compose
from core.frameworks.library import FrameworkLibrary, load_library
from core.frameworks.overlay import RepoOverlay

__all__ = [
    "DetectorRule",
    "EffectiveFramework",
    "FrameworkDefinition",
    "FrameworkLibrary",
    "HttpClientPatterns",
    "MockPatterns",
    "RepoOverlay",
    "RouteMountCall",
    "RoutePatterns",
    "TestPatterns",
    "compose",
    "detect_frameworks",
    "load_library",
]
