"""Endpoint strategy ABC, registry, and shared types.

Per-framework strategy implementations live under
`core/languages/<lang>/extractors/endpoints/` and self-register on
import. EndpointResolver triggers those imports through
`core.resolvers.endpoints.register_builtin_strategies()`.
"""

from core.resolvers.endpoints.strategy import (
    EndpointStrategy,
    get_strategy,
    register,
    registered_frameworks,
)
from core.resolvers.endpoints.types import ResolvedEndpoint


def register_builtin_strategies() -> None:
    """Import all shipped strategy modules so their `register(...)` calls
    run. Idempotent. Keep the imports inside the function — top-level
    imports here would force load order coupling on every consumer."""
    # Python decorator-style frameworks (fastapi, flask, …)
    from core.languages.python.extractors.endpoints import decorator  # noqa: F401

    # Java annotation-style frameworks (spring, …)
    from core.languages.java.extractors.endpoints import annotation  # noqa: F401


__all__ = [
    "EndpointStrategy",
    "ResolvedEndpoint",
    "get_strategy",
    "register",
    "register_builtin_strategies",
    "registered_frameworks",
]
