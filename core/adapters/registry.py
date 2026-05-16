"""AdapterRegistry: orchestrates registration, ordering, and execution of adapters."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.adapters.base import AdapterResult, IngestionAdapter, IngestionContext
from core.adapters.merger import MergedResult, ResultMerger
from core.adapters.validator import ResultValidator, ValidationReport
from core.types.errors import IngestionError

logger = logging.getLogger(__name__)


@dataclass
class RegisteredAdapter:
    adapter: IngestionAdapter
    enabled: bool = True


@dataclass
class RunReport:
    """End-to-end outcome of a registry.run_all() call."""

    results: list[AdapterResult] = field(default_factory=list)
    merged: MergedResult = field(default_factory=MergedResult)
    validation: ValidationReport = field(default_factory=ValidationReport)
    failures: dict[str, str] = field(default_factory=dict)
    """Adapter name -> error message for adapters that raised."""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return not self.failures and self.validation.ok


class AdapterRegistry:
    """Holds adapters and runs them in priority order.

    Failure isolation: one adapter's exception must not stop the others. We
    capture failures into `RunReport.failures` and continue. A run is `ok`
    only if every adapter succeeded AND validation passed; the caller decides
    whether a partial result is good enough to load.
    """

    def __init__(
        self,
        merger: ResultMerger | None = None,
        validator: ResultValidator | None = None,
    ) -> None:
        self._adapters: dict[str, RegisteredAdapter] = {}
        self._merger = merger or ResultMerger()
        self._validator = validator or ResultValidator()

    def register(self, adapter: IngestionAdapter, *, enabled: bool = True) -> None:
        ident = adapter.get_identifier()
        if ident in self._adapters:
            raise ValueError(f"adapter already registered: {ident}")
        self._adapters[ident] = RegisteredAdapter(adapter=adapter, enabled=enabled)

    def unregister(self, identifier: str) -> None:
        self._adapters.pop(identifier, None)

    def enable(self, identifier: str) -> None:
        self._adapters[identifier].enabled = True

    def disable(self, identifier: str) -> None:
        self._adapters[identifier].enabled = False

    def list_adapters(self) -> list[RegisteredAdapter]:
        return list(self._adapters.values())

    def run_all(self, context: IngestionContext | None = None) -> RunReport:
        report = RunReport()
        ctx = context or IngestionContext()
        ordered = sorted(
            (r for r in self._adapters.values() if r.enabled),
            key=lambda r: r.adapter.priority,
            reverse=True,
        )

        for registered in ordered:
            adapter = registered.adapter
            name = adapter.get_identifier()
            logger.info("running adapter: %s (priority=%d)", name, adapter.priority)
            t0 = time.perf_counter()
            try:
                result = adapter.extract(ctx)
                result.started_at = ctx.now
                result.finished_at = datetime.now(timezone.utc)
                result.warnings.extend(adapter.validate(result))
                report.results.append(result)
                logger.info(
                    "adapter %s done in %.2fs: %s",
                    name,
                    time.perf_counter() - t0,
                    result.counts(),
                )
            except IngestionError as exc:
                logger.exception("adapter %s failed", name)
                report.failures[name] = str(exc)
            except Exception as exc:  # noqa: BLE001 -- isolate adapter failures
                logger.exception("adapter %s raised unexpected error", name)
                report.failures[name] = f"{type(exc).__name__}: {exc}"

        report.merged = self._merger.merge(report.results)
        report.validation = self._validator.validate(report.merged)
        report.finished_at = datetime.now(timezone.utc)
        return report
