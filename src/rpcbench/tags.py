"""Client label and latest/safe/finalized snapshots. Not a disclosure finding."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rpcbench.consistency import parse_block_hash, parse_block_number
from rpcbench.freshness import Freshness, assess_freshness
from rpcbench.rpc import ProbeResult

BLOCK_TAGS: tuple[str, ...] = ("latest", "safe", "finalized")
CLIENT_METHOD = "web3_clientVersion"
# One clientVersion + one getBlockByNumber per tag. Not mixed into ranking.
META_REQUESTS_PER_ENDPOINT = 1 + len(BLOCK_TAGS)

_HEX_ONLY = re.compile(r"^0x[0-9a-fA-F]+$")


@dataclass(frozen=True)
class TagSnapshot:
    tag: str
    latency_ms: float | None
    height: int | None
    hash: str | None
    freshness: Freshness | None
    skipped: bool
    skip_reason: str | None


def parse_client_label(value: object) -> str | None:
    """Store a volunteered client string. Not a version/CVE check."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if _HEX_ONLY.fullmatch(text):
        return None
    if not any(ch.isalpha() for ch in text):
        return None
    if len(text) > 200:
        return text[:200]
    return text


def client_from_hit(hit: ProbeResult | None) -> str | None:
    if hit is None or not hit.ok:
        return None
    return parse_client_label(hit.result)


def skip_reason(hit: ProbeResult) -> str | None:
    if hit.ok and isinstance(hit.result, dict) and parse_block_number(hit.result) is not None:
        return None
    if hit.ok:
        return "empty"
    if hit.error_class == "jsonrpc":
        return "unsupported"
    return hit.error_class or "error"


def snapshots_from_hits(
    tag: str,
    hits: dict[str, ProbeResult],
    *,
    stale_blocks: int,
    block_time_s: float,
) -> dict[str, TagSnapshot]:
    heights = {
        name: parse_block_number(hit.result)
        if hit.ok and isinstance(hit.result, dict)
        else None
        for name, hit in hits.items()
    }
    judged = assess_freshness(
        heights, stale_blocks=stale_blocks, block_time_s=block_time_s
    )
    rows: dict[str, TagSnapshot] = {}
    for name, hit in hits.items():
        reason = skip_reason(hit)
        skipped = reason is not None
        rows[name] = TagSnapshot(
            tag=tag,
            latency_ms=hit.latency_ms,
            height=heights[name],
            hash=parse_block_hash(hit.result) if hit.ok and isinstance(hit.result, dict) else None,
            freshness=None if skipped else judged[name],
            skipped=skipped,
            skip_reason=reason,
        )
    return rows
