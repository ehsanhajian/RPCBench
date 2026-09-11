"""Pinned eth_getLogs range scaling. Extra read; not mixed into ranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rpcbench.methods import FAMILY_EVM, ZERO_ADDRESS
from rpcbench.rpc import ProbeResult

# Same four windows on every provider. Mix catalogs stay latest→latest (#42).
LOGS_RANGES: tuple[int, ...] = (1, 10, 100, 1000)
MAX_LOGS_RANGE = 1000
DEFAULT_LOGS_RANGE = 1000
# Common provider getLogs result cap. At-or-above is treated as truncated.
_TRUNCATION_N = 10_000
_TRUNCATION_MARKERS = (
    "too many results",
    "too many logs",
    "query returned more than",
    "log response size exceeded",
    "response size exceeded",
    "response too big",
    "range is too large",
    "block range is too wide",
    "block range too large",
    "exceeds the max block range",
    "max block range",
    "limit exceeded",
    "result truncated",
    "query timeout",
)


@dataclass(frozen=True)
class LogsRangeHit:
    """One pinned getLogs window. Skip means the HTTP call did not run (or budget)."""

    blocks: int
    from_block: int | None
    to_block: int | None
    ok: bool
    latency_ms: float | None
    error: str | None
    error_class: str | None
    bytes_in: int | None
    n_logs: int | None
    truncated: bool
    skip: str | None


def ranges_for(max_blocks: int) -> tuple[int, ...]:
    if max_blocks <= 0:
        return ()
    return tuple(span for span in LOGS_RANGES if span <= max_blocks)


def logs_window(pin: int | None, blocks: int) -> tuple[int, int] | None:
    """Inclusive [from, to] ending at pin. None if the chain is too short."""
    if pin is None or pin < 0 or blocks < 1:
        return None
    start = pin - blocks + 1
    if start < 0:
        return None
    return start, pin


def logs_filter(from_block: int, to_block: int) -> dict[str, str]:
    """Fixed address, no topics. Same payload on every provider."""
    return {
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "address": ZERO_ADDRESS,
    }


def skipped_range(
    blocks: int,
    reason: str,
    *,
    from_block: int | None = None,
    to_block: int | None = None,
) -> LogsRangeHit:
    return LogsRangeHit(
        blocks=blocks,
        from_block=from_block,
        to_block=to_block,
        ok=False,
        latency_ms=None,
        error=None,
        error_class=reason,
        bytes_in=None,
        n_logs=None,
        truncated=False,
        skip=reason,
    )


def hit_from_probe(
    blocks: int,
    from_block: int,
    to_block: int,
    probe: ProbeResult,
) -> LogsRangeHit:
    n_logs = _n_logs(probe.result) if probe.ok else None
    skip = None
    if probe.error_class in {"budget", "duration"}:
        skip = probe.error_class
    truncated = is_truncated(probe.error, n_logs)
    return LogsRangeHit(
        blocks=blocks,
        from_block=from_block,
        to_block=to_block,
        ok=bool(probe.ok),
        latency_ms=probe.latency_ms,
        error=probe.error,
        error_class=probe.error_class if skip is None else skip,
        bytes_in=probe.bytes_in,
        n_logs=n_logs,
        truncated=truncated,
        skip=skip,
    )


def is_truncated(error: str | None, n_logs: int | None) -> bool:
    if n_logs is not None and n_logs >= _TRUNCATION_N:
        return True
    if not error:
        return False
    blob = error.lower()
    return any(marker in blob for marker in _TRUNCATION_MARKERS)


def range_status(hit: LogsRangeHit) -> str:
    if hit.skip:
        return f"skip/{hit.skip}"
    if hit.truncated:
        return "trunc"
    if hit.ok:
        return "ok"
    return hit.error_class or "fail"


def family_skip_reason(family: str) -> str | None:
    if family != FAMILY_EVM:
        return "family"
    return None


def _n_logs(result: Any) -> int | None:
    if isinstance(result, list):
        return len(result)
    return None
