from __future__ import annotations

from rpcbench.freshness import (
    assess_freshness,
    block_time_for_chain,
    cohort_height,
    parse_block_height,
)


def test_parse_block_height() -> None:
    assert parse_block_height("0x10") == 16
    assert parse_block_height(20) == 20
    assert parse_block_height("20") == 20
    assert parse_block_height(None) is None
    assert parse_block_height("nope") is None
    assert parse_block_height(True) is None


def test_cohort_is_upper_median() -> None:
    assert cohort_height([90, 100]) == 100
    assert cohort_height([90, 100, 100]) == 100
    assert cohort_height([]) is None


def test_lag_and_stale_verdict() -> None:
    rows = assess_freshness(
        {"tip": 100, "lagged": 97, "dead": None},
        stale_blocks=2,
        block_time_s=12.0,
    )
    assert rows["tip"].verdict == "fresh"
    assert rows["tip"].lag_blocks == 0
    assert rows["lagged"].verdict == "stale"
    assert rows["lagged"].lag_blocks == 3
    assert rows["lagged"].lag_s == 36.0
    assert rows["dead"].verdict == "unknown"


def test_tolerance_keeps_small_lag_fresh() -> None:
    rows = assess_freshness({"a": 100, "b": 98}, stale_blocks=2, block_time_s=12.0)
    assert rows["b"].verdict == "fresh"
    assert rows["b"].lag_blocks == 2


def test_ahead_of_median_is_fresh() -> None:
    rows = assess_freshness({"ahead": 105, "tip": 100}, stale_blocks=2, block_time_s=12.0)
    assert rows["ahead"].lag_blocks == 0
    assert rows["ahead"].verdict == "fresh"


def test_block_time_per_chain() -> None:
    assert block_time_for_chain(1, None) == 12.0
    assert block_time_for_chain(137, None) == 2.0
    assert block_time_for_chain(137, 1.5) == 1.5
    assert block_time_for_chain(999, None) == 12.0
