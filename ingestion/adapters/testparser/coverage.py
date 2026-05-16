"""Estimate which services a test exercises (its 'affected_repos' set).

A test always exercises its own repo. Integration tests additionally exercise
the repos they import from or call out to. Without a real coverage tool we
approximate by inspecting imports: an integration test in `auth-service` that
imports `payment_client` is presumed to exercise `payment-service`.

This is a Phase 1 heuristic. A future revision can swap in real coverage.py
data when available.
"""

from __future__ import annotations

from dataclasses import dataclass

from ingestion.parsers.parser import ParsedTest


@dataclass
class CoverageEstimator:
    """Maps import-module names back to repo_ids.

    The caller supplies a mapping (typically built from the GitHub adapter's
    output) so the heuristic stays adapter-agnostic.
    """

    module_to_repo: dict[str, str]

    def estimate(self, parsed: ParsedTest, *, own_repo: str) -> tuple[str, ...]:
        repos = {own_repo}
        for module in parsed.imports:
            target = self.module_to_repo.get(module)
            if target and target != own_repo:
                repos.add(target)
        return tuple(sorted(repos))
