"""Single-run reliability score 0–100. Not an SLA and not a security score."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rpcbench.run import EndpointOutcome

ERROR_WEIGHT = 50
TIMEOUT_WEIGHT = 20
TAIL_WEIGHT = 20
COVERAGE_WEIGHT = 10
# p99/p50 of 1 → no tail penalty; 3 or more → full penalty.
TAIL_RATIO_CAP = 3.0


@dataclass(frozen=True)
class Reliability:
    score: int
    success_rate: float | None
    error_rate: float | None
    timeout_share: float
    p99_p50: float | None
    coverage: float
    errors: float
    timeouts: float
    tail: float
    coverage_points: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "success_rate": self.success_rate,
            "error_rate": self.error_rate,
            "timeout_share": self.timeout_share,
            "p99_p50": self.p99_p50,
            "coverage": self.coverage,
            "parts": {
                "errors": self.errors,
                "timeouts": self.timeouts,
                "tail": self.tail,
                "coverage": self.coverage_points,
            },
        }


def assess(outcome: EndpointOutcome) -> Reliability:
    stats = outcome.stats
    attempted = stats.n_ok + stats.n_fail
    error_rate = stats.error_rate
    success = None if error_rate is None else 1.0 - error_rate
    timeouts = dict(stats.by_class).get("timeout", 0)
    timeout_share = (timeouts / attempted) if attempted else 0.0
    coverage = _coverage(outcome)
    ratio = _p99_p50(stats.p50_ms, stats.p99_ms)
    if stats.n_ok == 0:
        return Reliability(
            score=0,
            success_rate=success,
            error_rate=error_rate,
            timeout_share=timeout_share,
            p99_p50=ratio,
            coverage=coverage,
            errors=0.0,
            timeouts=0.0,
            tail=0.0,
            coverage_points=0.0,
        )
    tail_bad = 0.0
    if ratio is not None:
        tail_bad = min(1.0, max(0.0, (ratio - 1.0) / (TAIL_RATIO_CAP - 1.0)))
    errors = ERROR_WEIGHT * (1.0 - (error_rate or 0.0))
    timeout_pts = TIMEOUT_WEIGHT * (1.0 - timeout_share)
    tail_pts = TAIL_WEIGHT * (1.0 - tail_bad)
    cov_pts = COVERAGE_WEIGHT * coverage
    raw = errors + timeout_pts + tail_pts + cov_pts
    score = int(max(0.0, min(100.0, round(raw))))
    return Reliability(
        score=score,
        success_rate=success,
        error_rate=error_rate,
        timeout_share=timeout_share,
        p99_p50=ratio,
        coverage=coverage,
        errors=errors,
        timeouts=timeout_pts,
        tail=tail_pts,
        coverage_points=cov_pts,
    )


def _p99_p50(p50: float | None, p99: float | None) -> float | None:
    if p50 is None or p99 is None or p50 <= 0:
        return None
    return p99 / p50


def _coverage(outcome: EndpointOutcome) -> float:
    if outcome.by_method:
        n = len(outcome.by_method)
        if n == 0:
            return 1.0 if outcome.stats.n_ok else 0.0
        return sum(1 for _name, stats in outcome.by_method if stats.n_ok) / n
    return 1.0 if outcome.stats.n_ok else 0.0
