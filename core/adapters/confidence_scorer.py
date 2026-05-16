"""Assign confidence scores to ingested ExternalConnections.

Confidence is consumed downstream by the impact rule engine when ranking
which services are 'really' affected. The scorer combines two signals:

1. Adapter trust: who saw it? Datadog APM traces are ground truth (real
   production calls), so they score high. Static analysis is inferential, so
   it scores lower. Documentation/NLP is lower still.
2. Edge quality: traced edges get a boost when call frequency is high (a
   service called 500x/min is more 'real' than one called once).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.types import ExternalConnection

_ADAPTER_TRUST: dict[str, float] = {
    "datadog": 0.95,
    "github": 0.75,
    "testparser": 0.70,
    "documentation": 0.55,
    "openapi": 0.80,
    "protobuf": 0.80,
}

DEFAULT_TRUST = 0.50


@dataclass
class ScoredConnection:
    connection: ExternalConnection
    confidence: float


class ConfidenceScorer:
    def __init__(self, adapter_trust: dict[str, float] | None = None) -> None:
        self._trust = dict(_ADAPTER_TRUST)
        if adapter_trust:
            self._trust.update(adapter_trust)

    def score(self, connection: ExternalConnection, *, source_adapter: str) -> float:
        trust = self._trust.get(source_adapter, DEFAULT_TRUST)
        frequency_boost = _frequency_boost(connection.frequency)
        return round(min(1.0, trust + frequency_boost), 4)

    def score_many(
        self, connections: list[ExternalConnection], *, source_adapter: str
    ) -> list[ScoredConnection]:
        return [
            ScoredConnection(connection=c, confidence=self.score(c, source_adapter=source_adapter))
            for c in connections
        ]


def _frequency_boost(freq_per_min: float) -> float:
    # Diminishing returns: 1 call/min ~ +0.01, 100 calls/min ~ +0.04, 1000+ ~ +0.05 cap.
    if freq_per_min <= 0:
        return 0.0
    if freq_per_min >= 1000:
        return 0.05
    if freq_per_min >= 100:
        return 0.04
    if freq_per_min >= 10:
        return 0.02
    return 0.01
