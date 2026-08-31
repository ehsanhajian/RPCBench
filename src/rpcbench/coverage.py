"""Workload coverage: which mix steps succeeded. Not a method-inventory scan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rpcbench.methods import CallSpec

if TYPE_CHECKING:
    from rpcbench.run import EndpointOutcome, LatencyStats, RunResult

# Tight on purpose: payload errors ("not supported" on a filter) stay a miss.
_NOT_OFFERED = (
    "method not found",
    "method does not exist",
    "method not exist",
    "does not exist/is not available",
    "unknown method",
)

STATUS_OK = "ok"
STATUS_MISS = "miss"
STATUS_SKIP = "skip"


@dataclass(frozen=True)
class CoverageCell:
    status: str
    n_ok: int
    n_fail: int
    error_class: str | None

    def label(self) -> str:
        if self.status == STATUS_OK:
            return "ok"
        if self.status == STATUS_SKIP:
            return "skip"
        return self.error_class or "miss"


def is_not_offered(error: str | None) -> bool:
    if not error:
        return False
    blob = error.lower()
    return any(marker in blob for marker in _NOT_OFFERED)


def coverage_steps(result: RunResult) -> tuple[CallSpec, ...]:
    if result.workload:
        return result.workload
    name = "head" if result.method == "eth_blockNumber" and not result.params else "call"
    return (CallSpec(name, result.method, tuple(result.params)),)


def is_coverage_miss(outcome: EndpointOutcome) -> bool:
    """A required mix step never succeeded. Empty by_method means single-method stats."""
    if not outcome.by_method:
        return False
    return any(stats.n_ok == 0 for _name, stats in outcome.by_method)


def missed_steps(outcome: EndpointOutcome, result: RunResult) -> tuple[str, ...]:
    return tuple(
        spec.name
        for spec in coverage_steps(result)
        if cell_for(outcome, spec, result).status != STATUS_OK
    )


def cell_for(
    outcome: EndpointOutcome, spec: CallSpec, result: RunResult
) -> CoverageCell:
    stats = _stats_for(outcome, spec, result)
    hits = [
        hit
        for hit in outcome.samples
        if (hit.method or result.method) == spec.method
    ]
    if stats.n_ok > 0:
        return CoverageCell(STATUS_OK, stats.n_ok, stats.n_fail, None)
    if stats.n_fail == 0 and not hits:
        return CoverageCell(STATUS_SKIP, 0, 0, None)
    failed = [hit for hit in hits if not hit.ok]
    if failed and all(is_not_offered(hit.error) for hit in failed):
        cls = failed[0].error_class
        return CoverageCell(STATUS_SKIP, stats.n_ok, stats.n_fail, cls)
    cls = stats.by_class[0][0] if stats.by_class else None
    return CoverageCell(STATUS_MISS, stats.n_ok, stats.n_fail, cls)


def as_dict(result: RunResult) -> dict[str, Any]:
    steps = coverage_steps(result)
    providers: list[dict[str, Any]] = []
    for outcome in result.outcomes:
        cells = {}
        missed: list[str] = []
        for spec in steps:
            cell = cell_for(outcome, spec, result)
            cells[spec.name] = {
                "status": cell.status,
                "n_ok": cell.n_ok,
                "n_fail": cell.n_fail,
                "error_class": cell.error_class,
                "method": spec.method,
            }
            if cell.status != STATUS_OK:
                missed.append(spec.name)
        providers.append(
            {
                "name": outcome.endpoint.name,
                "cells": cells,
                "missed": missed,
            }
        )
    return {
        "steps": [
            {"name": spec.name, "method": spec.method} for spec in steps
        ],
        "providers": providers,
    }


def _stats_for(
    outcome: EndpointOutcome, spec: CallSpec, result: RunResult
) -> LatencyStats:
    for name, stats in outcome.by_method:
        if name == spec.name:
            return stats
    if not outcome.by_method:
        return outcome.stats
    from rpcbench.run import summarize

    hits = tuple(
        hit
        for hit in outcome.samples
        if (hit.method or result.method) == spec.method
    )
    return summarize(hits)
