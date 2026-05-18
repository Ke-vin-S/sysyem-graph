"""ResolvedEndpoint dataclass — shared by strategies and the dispatcher.

Kept in its own module so per-framework strategy modules don't need to
import from `endpoint_resolver.py` (would create circular imports).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResolvedEndpoint:
    """One reconstructed HTTP endpoint.

    `derivation` is the audit trail — every fact ID that contributed. The
    receipt for "why does this endpoint look like
    `GET /v1/payments/users/{id}`?" — `tree.get(fact_id)` per entry.
    """

    method: str
    full_path: str
    handler_file: str
    handler_symbol: str
    framework: str
    confidence: float = 1.0
    derivation: tuple[str, ...] = field(default_factory=tuple)
