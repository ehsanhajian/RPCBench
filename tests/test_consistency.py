from __future__ import annotations

import pytest

from rpcbench.consistency import (
    BlockPinError,
    assess_consistency,
    canonical_hash,
    parse_block_hash,
    parse_block_pin,
)


HASH_A = "0x" + "aa" * 32
HASH_B = "0x" + "bb" * 32


def test_parse_block_hash_from_object_and_string() -> None:
    assert parse_block_hash({"hash": HASH_A, "number": "0x10"}) == HASH_A
    assert parse_block_hash(HASH_A.upper()) == HASH_A
    assert parse_block_hash("0x1") is None
    assert parse_block_hash(None) is None
    assert parse_block_hash({"number": "0x10"}) is None


def test_parse_block_pin_latest_is_cohort() -> None:
    assert parse_block_pin(None) is None
    assert parse_block_pin("latest") is None
    assert parse_block_pin("cohort") is None
    assert parse_block_pin("0x10") == 16
    assert parse_block_pin("100") == 100
    with pytest.raises(BlockPinError):
        parse_block_pin("nope")


def test_canonical_hash_needs_unique_majority() -> None:
    assert canonical_hash([HASH_A, HASH_A, HASH_B]) == HASH_A
    assert canonical_hash([HASH_A, HASH_B]) is None
    assert canonical_hash([HASH_A]) == HASH_A
    assert canonical_hash([]) is None


def test_majority_agrees_minority_disagrees() -> None:
    rows = assess_consistency(
        {"a": HASH_A, "b": HASH_A, "c": HASH_B, "dead": None},
        numbers={"a": 16, "b": 16, "c": 16, "dead": None},
        pin_height=16,
    )
    assert rows["a"].verdict == "agree"
    assert rows["b"].verdict == "agree"
    assert rows["c"].verdict == "disagree"
    assert rows["dead"].verdict == "unknown"
    assert rows["a"].canonical_hash == HASH_A
    assert rows["a"].pin_height == 16


def test_split_has_no_canonical() -> None:
    rows = assess_consistency(
        {"a": HASH_A, "b": HASH_B}, pin_height=16
    )
    assert rows["a"].verdict == "disagree"
    assert rows["b"].verdict == "disagree"
    assert rows["a"].canonical_hash is None
