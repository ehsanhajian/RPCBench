"""Cohort head freshness. Not sync monitoring and not a security finding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_STALE_BLOCKS = 2
DEFAULT_BLOCK_TIME_S = 12.0

# Seconds per block when the user does not pass --block-time.
# Override with --block-time / --stale-blocks for other chains.
BLOCK_TIME_BY_CHAIN: dict[int, float] = {
    1: 12.0,
    11155111: 12.0,
    10: 2.0,
    8453: 2.0,
    137: 2.0,
    56: 3.0,
    42161: 0.25,
}


@dataclass(frozen=True)
class Freshness:
    height: int | None
    height_hex: str | None
    lag_blocks: int | None
    lag_s: float | None
    verdict: str
    cohort_height: int | None


def parse_block_height(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            if text.startswith(("0x", "0X")):
                parsed = int(text, 16)
            elif text.isdigit():
                parsed = int(text)
            else:
                return None
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def to_height_hex(height: int | None) -> str | None:
    if height is None:
        return None
    return hex(height)


def cohort_height(heights: list[int]) -> int | None:
    """Upper-median head of the cohort. Empty input is unknown."""
    if not heights:
        return None
    ordered = sorted(heights)
    return ordered[len(ordered) // 2]


def block_time_for_chain(chain_id: int | None, override: float | None) -> float:
    if override is not None:
        return override
    if chain_id is not None and chain_id in BLOCK_TIME_BY_CHAIN:
        return BLOCK_TIME_BY_CHAIN[chain_id]
    return DEFAULT_BLOCK_TIME_S


def assess_freshness(
    heights: dict[str, int | None],
    *,
    stale_blocks: int = DEFAULT_STALE_BLOCKS,
    block_time_s: float = DEFAULT_BLOCK_TIME_S,
) -> dict[str, Freshness]:
    known = [h for h in heights.values() if h is not None]
    tip = cohort_height(known)
    rows: dict[str, Freshness] = {}
    for name, height in heights.items():
        if height is None or tip is None:
            rows[name] = Freshness(
                height=height,
                height_hex=to_height_hex(height),
                lag_blocks=None,
                lag_s=None,
                verdict="unknown",
                cohort_height=tip,
            )
            continue
        lag = max(0, tip - height)
        verdict = "stale" if lag > stale_blocks else "fresh"
        rows[name] = Freshness(
            height=height,
            height_hex=to_height_hex(height),
            lag_blocks=lag,
            lag_s=lag * block_time_s,
            verdict=verdict,
            cohort_height=tip,
        )
    return rows
