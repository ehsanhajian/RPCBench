"""Primary and fallback from a compare run. Not a security finding."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

from rpcbench.reliability import assess as assess_reliability
from rpcbench.verdict import READY, Verdict

if TYPE_CHECKING:
    from rpcbench.run import EndpointOutcome

_UNKNOWN_LAG = 10**9


@dataclass(frozen=True)
class Route:
    primary: str | None
    fallback: str | None
    why: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "primary": self.primary,
            "fallback": self.fallback,
            "why": self.why,
        }


def recommend(
    rows: list[tuple[EndpointOutcome, int | None, Verdict]],
) -> Route:
    """Pick primary and fallback among ready endpoints. Deterministic."""
    ready = [
        (index, outcome, rank, verdict)
        for index, (outcome, rank, verdict) in enumerate(rows)
        if verdict.decision == READY
    ]
    if not ready:
        return Route(
            None,
            None,
            "No ready endpoint this run; no primary or fallback.",
        )
    best_rank = min(rank for _, _, rank, _ in ready if rank is not None)
    candidates = [row for row in ready if row[2] == best_rank]
    candidates.sort(key=_primary_key)
    _, primary, _, p_verdict = candidates[0]
    rest = [row for row in ready if row[1].endpoint.name != primary.endpoint.name]
    primary_class = _fail_class(primary)
    if primary_class is None:
        pool = rest
    else:
        diverse = [row for row in rest if _fail_class(row[1]) != primary_class]
        pool = diverse if diverse else rest
    fallback = pool[0][1] if pool else None
    return Route(
        primary.endpoint.name,
        fallback.endpoint.name if fallback is not None else None,
        _why(primary, p_verdict, fallback),
    )


def html_block(route: Route) -> str:
    """Fragment for a later HTML report. Same names as JSON. No severity badges."""
    primary = route.primary or "none"
    fallback = route.fallback or "none"
    return (
        '<section aria-label="route">'
        f"<p>Primary {escape(primary)}</p>"
        f"<p>Fallback {escape(fallback)}</p>"
        f"<p>{escape(route.why)}</p>"
        "</section>"
    )


def _primary_key(
    row: tuple[int, EndpointOutcome, int | None, Verdict],
) -> tuple[int, int, int, int]:
    index, outcome, _, _ = row
    rel = assess_reliability(outcome).score
    lag = _lag(outcome)
    lag_key = lag if lag is not None else _UNKNOWN_LAG
    return (-rel, lag_key, _match_key(outcome), index)


def _lag(outcome: EndpointOutcome) -> int | None:
    fresh = outcome.freshness
    if fresh is None or fresh.lag_blocks is None:
        return None
    return fresh.lag_blocks


def _match_key(outcome: EndpointOutcome) -> int:
    cons = outcome.consistency
    if cons is None:
        return 1
    if cons.verdict == "agree":
        return 0
    return 2


def _fail_class(outcome: EndpointOutcome) -> str | None:
    by = dict(outcome.stats.by_class)
    if not by:
        return None
    return max(by.items(), key=lambda item: (item[1], item[0]))[0]


def _lag_label(outcome: EndpointOutcome) -> str:
    lag = _lag(outcome)
    return "—" if lag is None else str(lag)


def _match_label(outcome: EndpointOutcome) -> str:
    cons = outcome.consistency
    if cons is None:
        return "—"
    if cons.verdict == "agree":
        return "yes"
    if cons.verdict == "disagree":
        return "no"
    return "—"


def _why(
    primary: EndpointOutcome,
    p_verdict: Verdict,
    fallback: EndpointOutcome | None,
) -> str:
    rel = assess_reliability(primary).score
    head = (
        f"{primary.endpoint.name} is ready ({p_verdict.kind}, rel {rel}, "
        f"lag {_lag_label(primary)}, match {_match_label(primary)})"
    )
    if fallback is None:
        return f"{head}; no second ready endpoint for fallback."
    bit = f"{fallback.endpoint.name} is the next ready endpoint"
    primary_class = _fail_class(primary)
    fallback_class = _fail_class(fallback)
    if primary_class is None and fallback_class is None:
        return f"{head}. {bit}."
    if primary_class != fallback_class:
        shared = primary_class or fallback_class
        return f"{head}. {bit} and did not share a {shared} class."
    return f"{head}. {bit} (same {primary_class} class; no diverse ready alternative)."
