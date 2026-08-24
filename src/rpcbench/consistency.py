"""Cross-provider head-hash agreement. Not a security finding and not fork choice."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from rpcbench.freshness import parse_block_height, to_height_hex


@dataclass(frozen=True)
class Consistency:
    hash: str | None
    number: int | None
    verdict: str
    pin_height: int | None
    canonical_hash: str | None


class BlockPinError(ValueError):
    pass


def parse_block_pin(raw: str | None) -> int | None:
    """None / latest / cohort → use the run's median head. Else a block number."""
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.lower() in {"latest", "cohort"}:
        return None
    height = parse_block_height(text)
    if height is None:
        raise BlockPinError(
            f"invalid --block {raw!r} (hex, decimal, latest, or cohort)"
        )
    return height


def parse_block_hash(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("hash")
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith("0x"):
        hexpart = text[2:]
    else:
        hexpart = text
    if len(hexpart) != 64:
        return None
    try:
        int(hexpart, 16)
    except ValueError:
        return None
    return "0x" + hexpart


def parse_block_number(value: Any) -> int | None:
    if isinstance(value, dict):
        return parse_block_height(value.get("number"))
    return parse_block_height(value)


def canonical_hash(hashes: list[str]) -> str | None:
    """Unique majority hash. A tie is not a canonical result."""
    if not hashes:
        return None
    counts = Counter(hashes)
    best, n_best = counts.most_common(1)[0]
    n_second = counts.most_common(2)[1][1] if len(counts) > 1 else 0
    if n_best > n_second:
        return best
    return None


def assess_consistency(
    hashes: dict[str, str | None],
    *,
    numbers: dict[str, int | None] | None = None,
    pin_height: int | None,
) -> dict[str, Consistency]:
    known = [h for h in hashes.values() if h is not None]
    canon = canonical_hash(known)
    numbers = numbers or {}
    rows: dict[str, Consistency] = {}
    for name, digest in hashes.items():
        if digest is None:
            verdict = "unknown"
        elif canon is not None and digest == canon:
            verdict = "agree"
        else:
            verdict = "disagree"
        rows[name] = Consistency(
            hash=digest,
            number=numbers.get(name),
            verdict=verdict,
            pin_height=pin_height,
            canonical_hash=canon,
        )
    return rows


def short_hash(digest: str | None) -> str | None:
    if digest is None:
        return None
    return digest[:10]


def pin_hex(pin_height: int | None) -> str | None:
    return to_height_hex(pin_height)
