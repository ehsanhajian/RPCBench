from __future__ import annotations

import csv
import io
import json

import httpx

from rpcbench.config import parse_endpoints
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.logs import (
    LOGS_RANGES,
    is_truncated,
    logs_filter,
    logs_window,
    ranges_for,
)
from rpcbench.markdown import format_md
from rpcbench.methods import MIX_PROFILE, ZERO_ADDRESS
from rpcbench.report import format_run, run_to_dict
from rpcbench.run import run_endpoints


def _span(payload: dict) -> int | None:
    params = payload.get("params") or []
    if not params or not isinstance(params[0], dict):
        return None
    filt = params[0]
    try:
        start = int(filt["fromBlock"], 16)
        end = int(filt["toBlock"], 16)
    except (KeyError, TypeError, ValueError):
        return None
    return end - start + 1


def test_mix_logs_stay_latest_to_latest() -> None:
    logs = next(spec for spec in MIX_PROFILE if spec.name == "logs")
    filt = logs.params[0]
    assert filt["fromBlock"] == "latest"
    assert filt["toBlock"] == "latest"


def test_logs_windows_skip_when_head_is_too_low() -> None:
    assert logs_window(1000, 1000) == (1, 1000)
    assert logs_window(50, 1) == (50, 50)
    assert logs_window(50, 10) == (41, 50)
    assert logs_window(50, 100) is None
    assert logs_window(50, 1000) is None
    assert logs_window(None, 1) is None
    assert ranges_for(100) == (1, 10, 100)
    assert ranges_for(1000) == LOGS_RANGES


def test_truncation_markers() -> None:
    assert is_truncated("query returned more than 10000 results", None)
    assert is_truncated(None, 10_000)
    assert not is_truncated("method not found", 2)


def test_logs_range_fails_only_at_1000() -> None:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)}
            )
        if method == "eth_getLogs":
            span = _span(payload)
            assert span is not None
            seen.append(span)
            filt = payload["params"][0]
            assert filt["address"] == ZERO_ADDRESS
            assert filt["fromBlock"].startswith("0x")
            assert filt["toBlock"].startswith("0x")
            if span >= 1000 and str(request.url).endswith("/fat"):
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {
                            "code": -32005,
                            "message": "query returned more than 10000 results",
                        },
                    },
                )
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": []}
            )
        if method == "eth_getBlockByNumber":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"number": hex(1000), "hash": "0xabc"},
                },
            )
        if method == "web3_clientVersion":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "test/1"}
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "ok", "url": "http://127.0.0.1:8545/ok"},
                {"name": "fat", "url": "http://127.0.0.1:8545/fat"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=64,
        client=client,
        logs_range=1000,
    )
    assert result.logs_range == 1000
    assert result.pin_height == 1000
    assert set(seen) == {1, 10, 100, 1000}
    ok_row = next(o for o in result.outcomes if o.endpoint.name == "ok")
    fat = next(o for o in result.outcomes if o.endpoint.name == "fat")
    ok_hits = {hit.blocks: hit for hit in ok_row.logs_range}
    by_blocks = {hit.blocks: hit for hit in fat.logs_range}
    assert ok_hits[1000].ok
    assert by_blocks[1].ok
    assert by_blocks[10].ok
    assert by_blocks[100].ok
    assert not by_blocks[1000].ok
    assert by_blocks[1000].truncated
    assert by_blocks[1000].error_class == "jsonrpc"
    text = format_run(result, color=False)
    assert "Logs range" in text
    assert "1000" in text
    assert "jsonrpc" in text
    assert "trunc" in text
    data = run_to_dict(result)
    row = next(r for r in data["ranking"] if r["name"] == "fat")
    last = row["logs_range"][-1]
    assert last["blocks"] == 1000
    assert last["ok"] is False
    assert last["truncated"] is True
    assert last["status"] == "trunc"
    html = format_html(result)
    md = format_md(result)
    csv_row = next(
        r
        for r in csv.DictReader(io.StringIO(format_csv(result)))
        if r["name"] == "fat"
    )
    assert "Logs range" in html
    assert "## Logs range" in md
    assert csv_row["logs_1_status"] == "ok"
    assert csv_row["logs_1000_status"] == "trunc"
    assert csv_row["logs_1000_trunc"] == "true"
    assert "finding" not in text.lower()
    assert "finding" not in html.lower()
    assert "finding" not in md.lower()
    # Extra read is not mixed into ranking samples.
    assert result.outcomes[0].stats.n_ok == 1


def test_logs_range_skips_1000_when_head_is_short() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(50)}
            )
        if method == "eth_getLogs":
            raise AssertionError("100-block and 1000-block must skip, not send")
        if method == "eth_getBlockByNumber":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"number": hex(50), "hash": "0xabc"},
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    sent: list[int] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload.get("method") == "eth_getLogs":
            span = _span(payload)
            assert span in {1, 10}
            sent.append(span)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": []}
            )
        return handler(request)

    cfg = parse_endpoints(
        {"endpoints": [{"name": "young", "url": "http://127.0.0.1:8545"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=32, client=client, logs_range=1000
    )
    assert sent == [1, 10]
    hits = {hit.blocks: hit for hit in result.outcomes[0].logs_range}
    assert hits[1].ok
    assert hits[10].ok
    assert hits[100].skip == "head"
    assert hits[1000].skip == "head"
    text = format_run(result, color=False)
    assert "skip/head" in text


def test_logs_range_100_does_not_send_1000() -> None:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(5000)}
            )
        if method == "eth_getLogs":
            seen.append(_span(payload) or 0)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": []}
            )
        if method == "eth_getBlockByNumber":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"number": hex(5000), "hash": "0xabc"},
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=32,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        logs_range=100,
    )
    assert seen == [1, 10, 100]
    assert [hit.blocks for hit in result.outcomes[0].logs_range] == [1, 10, 100]


def test_logs_filter_is_fixed() -> None:
    filt = logs_filter(90, 100)
    assert filt["fromBlock"] == hex(90)
    assert filt["toBlock"] == hex(100)
    assert filt["address"] == ZERO_ADDRESS
    assert "topics" not in filt
