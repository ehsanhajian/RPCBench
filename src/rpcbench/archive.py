"""Historical-state capability probe. Extra read; not mixed into ranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rpcbench.freshness import parse_block_height
from rpcbench.methods import ZERO_ADDRESS
from rpcbench.rpc import ProbeResult

# Genesis state. Pruned full nodes typically keep ~128 recent blocks (Geth default).
ARCHIVE_BLOCK = 0
MIN_HEAD = 128
ARCHIVE_METHOD = "eth_getBalance"

STATUS_YES = "yes"
STATUS_NO = "no"
STATUS_UNKNOWN = "unknown"
STATUS_RATE_LIMITED = "rate_limited"

# Tight on purpose: generic "not found" stays unknown (could be method-not-found).
_PRUNE_MARKERS = (
    "missing trie node",
    "missing node",
    "historical state",
    "state is not available",
    "state not available",
    "no state available",
    "world state not available",
    "cannot get state",
    "could not find state",
    "does not have the state",
    "state trie",
    "pruned",
    "pruning",
    "requires archive",
    "need archive",
    "archive mode",
    "before the earliest",
    "oldest available block",
    "header not found",
    "block not found",
    "unknown block",
    "missing block",
)


@dataclass(frozen=True)
class ArchiveHit:
    """One genesis-state read. Skip means the HTTP call did not run (or budget)."""

    block: int
    method: str
    ok: bool
    latency_ms: float | None
    error: str | None
    error_class: str | None
    skip: str | None
    result: Any = None


def archive_params(block: int = ARCHIVE_BLOCK) -> tuple[str, str]:
    """Zero-address balance at an explicit historical block. Never 'latest'."""
    return (ZERO_ADDRESS, hex(block))


def archive_block(pin: int | None) -> int | None:
    """Genesis when the chain is long enough to distinguish prune vs archive."""
    if pin is None or pin < MIN_HEAD:
        return None
    return ARCHIVE_BLOCK


def skipped_archive(reason: str, *, block: int | None = None) -> ArchiveHit:
    return ArchiveHit(
        block=ARCHIVE_BLOCK if block is None else block,
        method=ARCHIVE_METHOD,
        ok=False,
        latency_ms=None,
        error=None,
        error_class=reason,
        skip=reason,
    )


def hit_from_probe(probe: ProbeResult, *, block: int = ARCHIVE_BLOCK) -> ArchiveHit:
    skip = None
    if probe.error_class in {"budget", "duration"}:
        skip = probe.error_class
    return ArchiveHit(
        block=block,
        method=ARCHIVE_METHOD,
        ok=bool(probe.ok),
        latency_ms=probe.latency_ms,
        error=probe.error,
        error_class=probe.error_class if skip is None else skip,
        skip=skip,
        result=probe.result,
    )


def is_pruned(error: str | None) -> bool:
    if not error:
        return False
    blob = error.lower()
    return any(marker in blob for marker in _PRUNE_MARKERS)


def archive_status(hit: ArchiveHit) -> str:
    """yes / no / unknown / rate_limited. Skip is unknown plus hit.skip."""
    if hit.skip:
        return STATUS_UNKNOWN
    if hit.ok and parse_block_height(hit.result) is not None:
        return STATUS_YES
    if hit.ok:
        return STATUS_UNKNOWN
    if hit.error_class == "rate_limit":
        return STATUS_RATE_LIMITED
    if is_pruned(hit.error):
        return STATUS_NO
    return STATUS_UNKNOWN


def archive_label(hit: ArchiveHit) -> str:
    if hit.skip:
        return f"skip/{hit.skip}"
    status = archive_status(hit)
    if status == STATUS_RATE_LIMITED:
        return "rate-limited"
    return status


def as_dict(hit: ArchiveHit) -> dict[str, Any]:
    return {
        "status": archive_status(hit),
        "label": archive_label(hit),
        "block": hit.block,
        "method": hit.method,
        "ok": hit.ok,
        "latency_ms": hit.latency_ms,
        "error": hit.error,
        "error_class": hit.error_class,
        "skip": hit.skip,
    }
