"""Cross-adapter validation: dangling refs, schema mismatches, suspicious data."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.adapters.merger import MergedResult


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class ResultValidator:
    """Validate a MergedResult prior to graph loading.

    The split between errors and warnings matters: errors block the load (we
    won't write a graph with dangling ExternalConnection.targetServiceId
    references). Warnings get logged but don't block — e.g. a Service with no
    tests is unusual but valid.
    """

    def validate(self, merged: MergedResult) -> ValidationReport:
        report = ValidationReport()
        service_ids = set(merged.services)

        for conn in merged.connections.values():
            if conn.source_service_id not in service_ids:
                report.errors.append(
                    f"connection {conn.id} references unknown source service "
                    f"{conn.source_service_id!r}"
                )
            if conn.target_service_id and conn.target_service_id not in service_ids:
                # External resources (3rd-party APIs) legitimately have no
                # corresponding Service; tolerate via target_name fallback.
                report.warnings.append(
                    f"connection {conn.id} targets unknown service "
                    f"{conn.target_service_id!r} (treating as external resource)"
                )

        # Artifacts and tests can legitimately reference a repo we haven't
        # yet seen a Service node for — e.g. a partial Phase 1 run with
        # testparser but no Datadog/GitHub. Flag as a warning so partial
        # ingestion completes; the graph loader will create stub Service
        # nodes for these repos when it writes.
        for artifact in merged.artifacts.values():
            if artifact.repo_id not in service_ids:
                report.warnings.append(
                    f"artifact {artifact.id} belongs to unknown repo {artifact.repo_id!r}"
                )

        for test in merged.tests.values():
            if test.repo_id not in service_ids:
                report.warnings.append(
                    f"test {test.id} belongs to unknown repo {test.repo_id!r}"
                )

        services_with_tests = {t.repo_id for t in merged.tests.values()}
        for sid in service_ids - services_with_tests:
            report.warnings.append(f"service {sid} has no tests ingested")

        return report
