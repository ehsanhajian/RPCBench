"""Production-readiness for this workload, this run. Not a security finding."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Any

from rpcbench.coverage import is_coverage_miss, missed_steps

if TYPE_CHECKING:
    from rpcbench.run import EndpointOutcome, RunResult

READY = "ready"
RISKY = "risky"
NOT_READY = "not_ready"

KIND_FAST = "fast+stable"
KIND_SLOW = "slow+reliable"
KIND_SIMILAR = "similar"
KIND_STALE = "stale"
KIND_STALE_RISK = "stale-risk"
KIND_TIMEOUT = "timeout"
KIND_RATE_LIMIT = "rate-limited"
KIND_COVERAGE = "coverage"
KIND_DISAGREE = "disagree"
KIND_JITTER = "jitter"
KIND_FAILED = "failed"
KIND_ERRORS = "errors"

# Jitter (stddev) above this fraction of P50 is unstable for the sample set.
JITTER_RATIO = 0.5
DEFAULT_BAND = 0.10

_CLI = {READY: "ready", RISKY: "risky", NOT_READY: "not ready"}


@dataclass(frozen=True)
class Signal:
    id: str
    problem: str
    why: str
    next: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "problem": self.problem,
            "why": self.why,
            "next": self.next,
        }


@dataclass(frozen=True)
class Verdict:
    decision: str
    kind: str
    signals: tuple[Signal, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "kind": self.kind,
            "signals": [row.as_dict() for row in self.signals],
        }

    def cli_decision(self) -> str:
        return _CLI[self.decision]


def assess(
    outcome: EndpointOutcome,
    *,
    rank: int | None,
    similar: bool,
    similar_band: float = DEFAULT_BAND,
    result: RunResult | None = None,
) -> Verdict:
    """Categorical decision plus operational signals. Deterministic for the same stats."""
    stats = outcome.stats
    classes = dict(stats.by_class)
    signals: list[Signal] = []

    if stats.n_ok == 0:
        signals.append(_failed_signal(classes))
    if _is_stale(outcome):
        signals.append(_STALE)
    elif _lag_blocks(outcome):
        signals.append(_STALE_RISK)
    if is_coverage_miss(outcome):
        steps = missed_steps(outcome, result) if result is not None else ()
        extra = f" ({', '.join(steps)})" if steps else ""
        signals.append(
            Signal(
                KIND_COVERAGE,
                f"A required mix method never succeeded{extra}",
                "This workload cannot finish on this endpoint (product fit, not a scan)",
                "Use an endpoint that answers those methods, or drop them from the mix",
            )
        )
    if _is_disagree(outcome):
        signals.append(_DISAGREE)
    if classes.get("rate_limit"):
        signals.append(_RATE_LIMIT)
    if classes.get("timeout") and stats.n_ok > 0:
        signals.append(_TIMEOUT)
    if _high_jitter(stats):
        signals.append(_JITTER)
    err = stats.error_rate
    if (
        stats.n_ok > 0
        and err is not None
        and err > similar_band
        and not classes.get("timeout")
        and not classes.get("rate_limit")
    ):
        signals.append(_ERRORS)

    if (
        stats.n_ok == 0
        or _is_stale(outcome)
        or is_coverage_miss(outcome)
        or _is_disagree(outcome)
    ):
        decision = NOT_READY
        kind = _not_ready_kind(outcome, classes)
    elif any(row.id in _RISKY_IDS for row in signals):
        decision = RISKY
        kind = next(row.id for row in signals if row.id in _RISKY_IDS)
    elif rank == 1 and similar:
        decision = READY
        kind = KIND_SIMILAR
    elif rank == 1:
        decision = READY
        kind = KIND_FAST
    else:
        decision = READY
        kind = KIND_SLOW
    return Verdict(decision, kind, tuple(signals))


def html_block(rows: list[tuple[str, Verdict]]) -> str:
    """Fragment for a later HTML report. Same decisions as JSON. No severity badges."""
    parts = ['<section aria-label="verdict">']
    for name, verdict in rows:
        parts.append(
            f"<article><h3>{escape(name)}</h3>"
            f"<p>{escape(verdict.cli_decision())} · {escape(verdict.kind)}</p>"
        )
        for sig in verdict.signals:
            parts.append(
                "<dl>"
                f"<dt>problem</dt><dd>{escape(sig.problem)}</dd>"
                f"<dt>why</dt><dd>{escape(sig.why)}</dd>"
                f"<dt>next</dt><dd>{escape(sig.next)}</dd>"
                "</dl>"
            )
        parts.append("</article>")
    parts.append("</section>")
    return "".join(parts)


def _is_stale(outcome: EndpointOutcome) -> bool:
    fresh = outcome.freshness
    return fresh is not None and fresh.verdict == "stale"


def _is_disagree(outcome: EndpointOutcome) -> bool:
    cons = outcome.consistency
    return cons is not None and cons.verdict == "disagree"


def _not_ready_kind(outcome: EndpointOutcome, classes: dict[str, int]) -> str:
    if outcome.stats.n_ok == 0:
        if classes.get("timeout"):
            return KIND_TIMEOUT
        if classes.get("rate_limit"):
            return KIND_RATE_LIMIT
        return KIND_FAILED
    if _is_stale(outcome):
        return KIND_STALE
    if is_coverage_miss(outcome):
        return KIND_COVERAGE
    return KIND_DISAGREE


def _lag_blocks(outcome: EndpointOutcome) -> bool:
    fresh = outcome.freshness
    return bool(
        fresh is not None
        and fresh.verdict == "fresh"
        and fresh.lag_blocks is not None
        and fresh.lag_blocks > 0
    )


def _high_jitter(stats: Any) -> bool:
    if stats.n_ok < 2 or stats.jitter_ms is None or not stats.p50_ms:
        return False
    return stats.jitter_ms > JITTER_RATIO * stats.p50_ms


def _failed_signal(classes: dict[str, int]) -> Signal:
    if classes.get("timeout"):
        return _TIMEOUT_ALL
    if classes.get("rate_limit"):
        return _RATE_LIMIT
    return _FAILED


_TIMEOUT = Signal(
    KIND_TIMEOUT,
    "Some timed samples timed out",
    "This mix will stall or drop calls under the same budget",
    "Route this workload to another endpoint, or raise --timeout",
)
_TIMEOUT_ALL = Signal(
    KIND_TIMEOUT,
    "No successful timed samples (timeouts)",
    "This endpoint did not answer the workload in time",
    "Route this workload to another endpoint, or raise --timeout",
)
_RATE_LIMIT = Signal(
    KIND_RATE_LIMIT,
    "Timed samples returned 429 / CU throttle",
    "The same request budget will stall or retry",
    "Spread requests, raise the provider quota, or pick another endpoint",
)
_STALE = Signal(
    KIND_STALE,
    "Head lag exceeds --stale-blocks vs the cohort",
    "Reads can be behind the chain tip in this compare window",
    "Prefer a fresher endpoint for this mix (not validator monitoring)",
)
_STALE_RISK = Signal(
    KIND_STALE_RISK,
    "Head is behind the cohort but still within --stale-blocks",
    "Lag may cross stale on the next blocks",
    "Watch lag, or pin --block if heads naturally diverge by one",
)
_DISAGREE = Signal(
    KIND_DISAGREE,
    "Pinned block hash does not match the cohort",
    "Compare-time data disagreement, not fork choice",
    "Pin --block, or exclude this endpoint for consistency-sensitive reads",
)
_JITTER = Signal(
    KIND_JITTER,
    "Jitter (stddev) is above half of P50",
    "Tail latency is unstable for this sample set",
    "Prefer a stabler endpoint, or take a longer --budget",
)
_ERRORS = Signal(
    KIND_ERRORS,
    "Timed error rate is above the similar-band",
    "This mix is not stable enough to prefer this endpoint",
    "Prefer an endpoint with err within the band, or raise --samples",
)
_FAILED = Signal(
    KIND_FAILED,
    "No successful timed samples",
    "This endpoint did not answer the workload",
    "Check the URL is reachable from this machine (localhost is allowed)",
)

_RISKY_IDS = frozenset(
    {
        KIND_RATE_LIMIT,
        KIND_TIMEOUT,
        KIND_STALE_RISK,
        KIND_JITTER,
        KIND_ERRORS,
    }
)
