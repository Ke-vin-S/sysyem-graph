"""Merge AdapterResults from multiple adapters into a single deduplicated view."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.adapters.base import AdapterResult
from core.types import CodeArtifact, ExternalConnection, Service, TestCase


@dataclass
class MergedResult:
    services: dict[str, Service] = field(default_factory=dict)
    connections: dict[str, ExternalConnection] = field(default_factory=dict)
    artifacts: dict[str, CodeArtifact] = field(default_factory=dict)
    tests: dict[str, TestCase] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    """Human-readable notes about which adapter won each conflict."""

    def counts(self) -> dict[str, int]:
        return {
            "services": len(self.services),
            "connections": len(self.connections),
            "artifacts": len(self.artifacts),
            "tests": len(self.tests),
            "conflicts": len(self.conflicts),
        }


class ResultMerger:
    """Deduplicate by ID; on conflict, the higher-priority adapter wins.

    Adapters arrive pre-sorted by descending priority from the registry, so
    we just keep the first occurrence and record any losers in `conflicts`.
    """

    def merge(self, results: list[AdapterResult]) -> MergedResult:
        merged = MergedResult()
        for result in results:
            _accumulate(merged.services, result.services, result.adapter, "service", merged.conflicts)
            _accumulate(
                merged.connections,
                result.connections,
                result.adapter,
                "connection",
                merged.conflicts,
            )
            _accumulate(merged.artifacts, result.artifacts, result.adapter, "artifact", merged.conflicts)
            _accumulate(merged.tests, result.tests, result.adapter, "test", merged.conflicts)
        return merged


def _accumulate(
    bucket: dict[str, object],
    items: list,  # type: ignore[type-arg]
    adapter: str,
    kind: str,
    conflicts: list[str],
) -> None:
    for item in items:
        item_id = item.id  # type: ignore[attr-defined]
        if item_id in bucket:
            conflicts.append(f"{kind} {item_id}: kept higher-priority value; {adapter} skipped")
            continue
        bucket[item_id] = item
