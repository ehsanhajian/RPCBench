from __future__ import annotations

from rpcbench.rpc import ProbeResult
from rpcbench.tags import (
    BLOCK_TAGS,
    client_from_hit,
    parse_client_label,
    skip_reason,
    snapshots_from_hits,
)


HASH_A = "0x" + "aa" * 32


def _hit(*, ok: bool, result=None, error_class=None, error=None, ms: float = 5.0) -> ProbeResult:
    return ProbeResult(
        ok=ok,
        reachable=ok,
        latency_ms=ms,
        result=result,
        error=error,
        error_class=error_class,
        attempts=1,
        method="eth_getBlockByNumber",
    )


def test_parse_client_label_is_metadata_only() -> None:
    assert parse_client_label("Geth/v1.14.12-stable") == "Geth/v1.14.12-stable"
    assert parse_client_label("  erigon/2.60.0  ") == "erigon/2.60.0"
    assert parse_client_label("0x2a") is None
    assert parse_client_label("123") is None
    assert parse_client_label(None) is None
    assert parse_client_label({"version": "Geth"}) is None


def test_client_from_hit() -> None:
    ok = ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=5.0,
        result="Nethermind/1.25.0",
        error=None,
        error_class=None,
        attempts=1,
        method="web3_clientVersion",
    )
    assert client_from_hit(ok) == "Nethermind/1.25.0"
    assert client_from_hit(_hit(ok=False, error_class="jsonrpc", error="no")) is None


def test_skip_reason_unsupported_on_jsonrpc() -> None:
    miss = _hit(ok=False, error_class="jsonrpc", error="Unknown block tag")
    assert skip_reason(miss) == "unsupported"
    empty = _hit(ok=True, result=None)
    assert skip_reason(empty) == "empty"
    block = _hit(ok=True, result={"number": "0x64", "hash": HASH_A})
    assert skip_reason(block) is None


def test_snapshots_freshness_is_per_tag() -> None:
    hits = {
        "tip": _hit(ok=True, result={"number": "0x64", "hash": HASH_A}, ms=12.0),
        "lag": _hit(ok=True, result={"number": "0x61", "hash": HASH_A}, ms=9.0),
        "dead": _hit(ok=False, error_class="jsonrpc", error="no tag", ms=4.0),
    }
    rows = snapshots_from_hits(
        "finalized", hits, stale_blocks=2, block_time_s=12.0
    )
    assert list(BLOCK_TAGS) == ["latest", "safe", "finalized"]
    assert rows["tip"].skipped is False
    assert rows["tip"].freshness is not None
    assert rows["tip"].freshness.verdict == "fresh"
    assert rows["lag"].freshness is not None
    assert rows["lag"].freshness.verdict == "stale"
    assert rows["lag"].freshness.lag_blocks == 3
    assert rows["dead"].skipped is True
    assert rows["dead"].skip_reason == "unsupported"
    assert rows["dead"].freshness is None
