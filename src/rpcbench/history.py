"""Timed historical-state reads vs latest. Extra read; not mixed into ranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rpcbench.archive import (
    MIN_HEAD,
    ArchiveHit,
    archive_status,
    is_pruned,
)
from rpcbench.freshness import parse_block_height
from rpcbench.logs import family_skip_reason
from rpcbench.methods import ZERO_ADDRESS
from rpcbench.rpc import ProbeResult

DEFAULT_LOOKBACK = 1000
HISTORY_METHOD = "eth_getBalance"


@dataclass(frozen=True)
class HistoryHit:
    """One historical getBalance vs a latest getBalance. Skip means no historical HTTP."""

    lookback: int
    block: int | None
    ok: bool
    latency_ms: float | None
    head_ms: float | None
    ratio: float | None
    error: str | None
    error_class: str | None
    skip: str | None
    n_ok: int
    n_fail: int


def history_block(pin: int | None, lookback: int) -> int | None:
    """Absolute height pin − lookback. None if the chain is too short."""
    if pin is None or lookback < 1 or pin < lookback:
        return None
    return pin - lookback


def history_params(block: int) -> tuple[str, str]:
    return (ZERO_ADDRESS, hex(block))


def latest_params() -> tuple[str, str]:
    return (ZERO_ADDRESS, "latest")


def history_skip_reason(
    *,
    pin: int | None,
    lookback: int,
    family: str,
    archive: ArchiveHit | None,
) -> str | None:
    """Why the historical read should not run. None means send it."""
    reason = family_skip_reason(family)
    if reason:
        return reason
    if lookback < 1:
        return None
    if pin is None:
        return "pin"
    if history_block(pin, lookback) is None:
        return "head"
    if archive is None or lookback < MIN_HEAD:
        return None
    status = archive_status(archive)
    if status == "no":
        return "archive"
    if status == "rate_limited":
        return "rate_limit"
    return None


def skipped_history(
    reason: str, *, lookback: int, block: int | None = None
) -> HistoryHit:
    return HistoryHit(
        lookback=lookback,
        block=block,
        ok=False,
        latency_ms=None,
        head_ms=None,
        ratio=None,
        error=None,
        error_class=reason,
        skip=reason,
        n_ok=0,
        n_fail=0,
    )


def hit_from_probes(
    *,
    lookback: int,
    block: int,
    historical: ProbeResult,
    head: ProbeResult | None,
) -> HistoryHit:
    skip = None
    if historical.error_class in {"budget", "duration"}:
        skip = historical.error_class
    hist_ok = bool(historical.ok) and parse_block_height(historical.result) is not None
    if not hist_ok and is_pruned(historical.error) and skip is None:
        # Pruned at this lookback is skip/archive, not a ranking miss.
        skip = "archive"
    head_ms = head.latency_ms if head is not None and head.ok else None
    hist_ms = historical.latency_ms if hist_ok else None
    ratio = None
    if hist_ms is not None and head_ms and head_ms > 0:
        ratio = hist_ms / head_ms
    n_ok = 1 if hist_ok else 0
    n_fail = 0 if skip or hist_ok else 1
    error = historical.error
    error_class = skip or historical.error_class
    if skip == "archive" and not error_class:
        error_class = "archive"
    return HistoryHit(
        lookback=lookback,
        block=block,
        ok=hist_ok,
        latency_ms=hist_ms,
        head_ms=head_ms,
        ratio=ratio,
        error=error,
        error_class=error_class,
        skip=skip,
        n_ok=n_ok,
        n_fail=n_fail,
    )


def history_error_rate(hit: HistoryHit) -> float | None:
    attempted = hit.n_ok + hit.n_fail
    if attempted == 0:
        return None
    return hit.n_fail / attempted


def history_label(hit: HistoryHit) -> str:
    if hit.skip:
        return f"skip/{hit.skip}"
    if hit.ok:
        return "ok"
    return hit.error_class or "fail"


def as_dict(hit: HistoryHit) -> dict[str, Any]:
    return {
        "lookback": hit.lookback,
        "block": hit.block,
        "ok": hit.ok,
        "latency_ms": hit.latency_ms,
        "head_ms": hit.head_ms,
        "ratio": hit.ratio,
        "error_rate": history_error_rate(hit),
        "n_ok": hit.n_ok,
        "n_fail": hit.n_fail,
        "error": hit.error,
        "error_class": hit.error_class,
        "skip": hit.skip,
        "status": history_label(hit),
    }
