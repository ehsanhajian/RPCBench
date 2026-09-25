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
        digest = (
            value.get("hash")
            or value.get("blockhash")
            or value.get("block_hash")
            or value.get("digest")
        )
        if digest is None:
            header = value.get("header")
            if isinstance(header, dict):
                digest = header.get("hash")
        if digest is None:
            block_id = value.get("block_id")
            if isinstance(block_id, dict):
                digest = block_id.get("hash")
        if digest is None:
            sync = value.get("sync_info")
            if isinstance(sync, dict):
                digest = sync.get("latest_block_hash")
        value = digest
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    lower = text.lower()
    if lower.startswith("0x"):
        hexpart = lower[2:]
    else:
        hexpart = lower
    if len(hexpart) == 64:
        try:
            int(hexpart, 16)
        except ValueError:
            pass
        else:
            return "0x" + hexpart
    # Solana blockhash: base58, typically 32–44 chars.
    if 32 <= len(text) <= 64 and all(
        ch in "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz" for ch in text
    ):
        return text
    return None


def parse_block_number(value: Any) -> int | None:
    if isinstance(value, dict):
        height = parse_block_height(value)
        if height is not None:
            return height
        height = parse_block_height(value.get("blockHeight"))
        if height is not None:
            return height
        return parse_block_height(value.get("parentSlot"))
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
