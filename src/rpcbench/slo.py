"""SLO budgets for CI gates. Not an SLA certificate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rpcbench.run import EndpointOutcome, RunResult


@dataclass(frozen=True)
class BudgetMiss:
    """One endpoint missed one budget."""

    name: str
    budget: str
    limit: float
    value: float | None
    detail: str


@dataclass(frozen=True)
class SloReport:
    """Result of evaluating optional latency / error / lag budgets."""

    misses: tuple[BudgetMiss, ...]
    checked: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.misses

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": list(self.checked),
            "misses": [
                {
                    "name": m.name,
                    "budget": m.budget,
                    "limit": m.limit,
                    "value": m.value,
                    "detail": m.detail,
                }
                for m in self.misses
            ],
        }


def evaluate_slo(
    result: RunResult,
    *,
    max_p95_ms: float | None = None,
    max_error_rate: float | None = None,
    max_lag_blocks: float | None = None,
    endpoint: str | None = None,
) -> SloReport:
    """Compare outcomes to budgets. Empty misses means pass.

    When ``endpoint`` is set, only that name is checked. Otherwise every
    configured endpoint is checked (including total failures).
    """
    if max_p95_ms is None and max_error_rate is None and max_lag_blocks is None:
        return SloReport(misses=(), checked=())

    outcomes = list(result.outcomes)
    if endpoint:
        outcomes = [o for o in outcomes if o.endpoint.name == endpoint]
        if not outcomes:
            miss = BudgetMiss(
                name=endpoint,
                budget="endpoint",
                limit=0.0,
                value=None,
                detail=f"no endpoint named {endpoint!r} in this run",
            )
            return SloReport(misses=(miss,), checked=(endpoint,))

    misses: list[BudgetMiss] = []
    checked = tuple(o.endpoint.name for o in outcomes)
    for outcome in outcomes:
        misses.extend(
            _check_outcome(
                outcome,
                max_p95_ms=max_p95_ms,
                max_error_rate=max_error_rate,
                max_lag_blocks=max_lag_blocks,
            )
        )
    return SloReport(misses=tuple(misses), checked=checked)


def format_slo_failures(report: SloReport) -> str:
    """Human lines for stderr / CLI footer."""
    if report.ok:
        return "SLO  ok"
    lines = ["SLO  failed"]
    for miss in report.misses:
        value = "—" if miss.value is None else _fmt(miss.value)
        lines.append(
            f"  {miss.name}  {miss.budget}={value}  limit={_fmt(miss.limit)}  "
            f"{miss.detail}"
        )
    return "\n".join(lines)


def _check_outcome(
    outcome: EndpointOutcome,
    *,
    max_p95_ms: float | None,
    max_error_rate: float | None,
    max_lag_blocks: float | None,
) -> list[BudgetMiss]:
    name = outcome.endpoint.name
    stats = outcome.stats
    misses: list[BudgetMiss] = []
    if max_p95_ms is not None:
        if stats.n_ok < 1:
            misses.append(
                BudgetMiss(
                    name=name,
                    budget="p95",
                    limit=max_p95_ms,
                    value=None,
                    detail="no successful samples",
                )
            )
        elif stats.p95_ms > max_p95_ms:
            misses.append(
                BudgetMiss(
                    name=name,
                    budget="p95",
                    limit=max_p95_ms,
                    value=stats.p95_ms,
                    detail=f"p95 {stats.p95_ms:.3f}ms > {max_p95_ms:g}ms",
                )
            )
    if max_error_rate is not None:
        rate = float(stats.error_rate)
        if rate > max_error_rate:
            misses.append(
                BudgetMiss(
                    name=name,
                    budget="error_rate",
                    limit=max_error_rate,
                    value=rate,
                    detail=f"error_rate {rate:.3f} > {max_error_rate:g}",
                )
            )
    if max_lag_blocks is not None:
        fresh = outcome.freshness
        lag = None if fresh is None else fresh.lag_blocks
        if lag is None:
            misses.append(
                BudgetMiss(
                    name=name,
                    budget="lag",
                    limit=max_lag_blocks,
                    value=None,
                    detail="head lag unknown",
                )
            )
        elif lag > max_lag_blocks:
            misses.append(
                BudgetMiss(
                    name=name,
                    budget="lag",
                    limit=max_lag_blocks,
                    value=float(lag),
                    detail=f"lag {lag} blocks > {max_lag_blocks:g}",
                )
            )
    return misses


def _fmt(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{value:g}"
    return f"{value:.3f}"
