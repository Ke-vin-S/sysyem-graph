"""EndpointResolver: dispatch to per-framework strategies.

The reconstruction work for each framework lives in
`core.resolvers.endpoints.*` (the ABC + registry) and
`core/languages/<lang>/extractors/endpoints/<style>.py` (the strategy
implementations). EndpointResolver itself does only one thing: for each
detected framework, look up its strategy and run it.

Adding a new framework means dropping one YAML and one strategy module —
no edits to this file or any cross-cutting resolver.
"""

from __future__ import annotations

import logging

from core.resolvers.endpoints import (
    ResolvedEndpoint,
    get_strategy,
    register_builtin_strategies,
)
from core.resolvers.resolver import ResolverContext

__all__ = ["EndpointResolver", "ResolvedEndpoint"]

logger = logging.getLogger(__name__)

# Trigger strategy registration at module import. Idempotent.
register_builtin_strategies()


class EndpointResolver:
    def resolve(self, context: ResolverContext) -> list[ResolvedEndpoint]:
        results: list[ResolvedEndpoint] = []
        for fw in context.frameworks:
            if fw.routes is None:
                continue
            strategy = get_strategy(fw.name)
            if strategy is None:
                logger.debug(
                    "endpoint_resolver: no strategy registered for framework %r", fw.name
                )
                continue
            results.extend(
                strategy.resolve(tree=context.tree, fw=fw, repo_id=context.repo_id)
            )
        return results
