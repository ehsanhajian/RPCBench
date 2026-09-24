from __future__ import annotations

import csv
import io
import json
import time

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.markdown import format_md
from rpcbench.report import format_run, run_to_dict
from rpcbench.run import run_endpoints
from rpcbench.websocket import missed_heads, probe_websocket, websocket_label


def _ok(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    ident = payload.get("id", 1) if isinstance(payload, dict) else 1
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": ident, "result": "0x1"},
    )


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_ok))


def _ack(sub: str = "0xabc") -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": sub}


def _head(number: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "eth_subscription",
        "params": {"subscription": "0xabc", "result": {"number": hex(number)}},
    }


class ScriptedWS:
    def __init__(self, script: list, *, recv_delay: float = 0.0):
        self.script = list(script)
        self.sent: list[str] = []
        self.recv_delay = recv_delay

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None):
        if self.recv_delay:
            time.sleep(self.recv_delay)
            self.recv_delay = 0.0
        if not self.script:
            if timeout and timeout > 0:
                time.sleep(min(timeout, 0.01))
            raise TimeoutError
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, dict):
            return json.dumps(item)
        return item


def _open(script: list, *, fail: BaseException | None = None, connect_delay: float = 0.0):
    captured: dict = {}

    def open_ws(url: str, *, headers=(), timeout: float = 10.0):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        if connect_delay:
            time.sleep(connect_delay)
        if fail is not None:
            raise fail
        return ScriptedWS(script)

    open_ws.captured = captured  # type: ignore[attr-defined]
    return open_ws


def _cfg(*, ws: str | None = "ws://127.0.0.1:8546", headers=None):
    item = {"name": "local", "url": "http://127.0.0.1:1"}
    if ws:
        item["ws"] = ws
    if headers:
        item["headers"] = headers
    return parse_endpoints({"endpoints": [item]})


def test_missed_heads_counts_gaps() -> None:
    assert missed_heads([]) == 0
    assert missed_heads([16]) == 0
    assert missed_heads([16, 17, 18]) == 0
    assert missed_heads([16, 18]) == 1
    assert missed_heads([10, 12, 15]) == 3


def test_websocket_reports_connect_subscribe_and_first_event() -> None:
    opener = _open([_ack(), _head(16), _head(17)], connect_delay=0.02)
    result = run_endpoints(
        _cfg(),
        samples=1,
        warmup=0,
        budget=32,
        websocket=0.05,
        client=_client(),
        open_ws=opener,
    )
    assert result.websocket == pytest.approx(0.05)
    hit = result.outcomes[0].websocket
    assert hit is not None
    assert hit.ok
    assert hit.connect_ms is not None and hit.connect_ms >= 15
    assert hit.subscribe_ms is not None and hit.subscribe_ms >= 0
    assert hit.first_event_ms is not None
    assert hit.n_events == 2
    assert hit.missed == 0
    assert hit.disconnects == 0
    assert websocket_label(hit) == "ok"
    compact = format_run(result, color=False)
    assert "websocket=0.05" in compact
    table = compact.split("WebSocket", 1)[1]
    assert "connect" in table
    assert "sub" in table
    assert "first" in table
    assert "ok" in table
    assert "finding" not in compact.lower()
    data = run_to_dict(result)
    assert data["websocket"] == pytest.approx(0.05)
    blob = data["ranking"][0]["websocket"]
    assert blob["connect_ms"] is not None
    assert blob["subscribe_ms"] is not None
    assert blob["first_event_ms"] is not None
    assert blob["n_events"] == 2
    assert blob["status"] == "ok"
    html = format_html(result)
    assert ">WebSocket</h2>" in html
    assert "not mixed into ranking" in html
    md = format_md(result)
    assert "## WebSocket" in md
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["websocket_status"] == "ok"
    assert int(rows[0]["websocket_n"]) == 2
    assert float(rows[0]["websocket_connect_ms"]) > 0


def test_websocket_missing_url_is_not_configured() -> None:
    result = run_endpoints(
        _cfg(ws=None),
        samples=1,
        warmup=0,
        budget=32,
        websocket=0.05,
        client=_client(),
        open_ws=_open([_ack()]),
    )
    hit = result.outcomes[0].websocket
    assert hit is not None
    assert not hit.ok
    assert hit.skip == "config"
    assert websocket_label(hit) == "not configured"
    compact = format_run(result, color=False)
    table = compact.split("WebSocket", 1)[1]
    assert "not configured" in table
    data = run_to_dict(result)
    assert data["ranking"][0]["websocket"]["status"] == "not configured"
    html = format_html(result)
    assert "not configured" in html
    md = format_md(result)
    assert "not configured" in md
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["websocket_status"] == "not configured"
    assert "finding" not in compact.lower()


def test_websocket_counts_missed_heads_and_disconnects() -> None:
    opener = _open([_ack(), _head(16), _head(18), ConnectionError("closed")])
    result = run_endpoints(
        _cfg(),
        samples=1,
        warmup=0,
        budget=32,
        websocket=0.05,
        client=_client(),
        open_ws=opener,
    )
    hit = result.outcomes[0].websocket
    assert hit is not None
    assert hit.ok
    assert hit.n_events == 2
    assert hit.missed == 1
    assert hit.disconnects == 1
    compact = format_run(result, color=False)
    table = compact.split("WebSocket", 1)[1]
    assert "1" in table


def test_websocket_window_is_bounded() -> None:
    started = time.monotonic()
    result = run_endpoints(
        _cfg(),
        samples=1,
        warmup=0,
        budget=32,
        websocket=0.05,
        client=_client(),
        open_ws=_open([_ack()]),
    )
    elapsed = time.monotonic() - started
    hit = result.outcomes[0].websocket
    assert hit is not None
    assert hit.ok
    assert hit.n_events == 0
    assert elapsed < 1.0
    assert result.websocket == pytest.approx(0.05)


def test_websocket_off_omits_section() -> None:
    result = run_endpoints(
        _cfg(),
        samples=1,
        warmup=0,
        budget=32,
        client=_client(),
        open_ws=_open([_ack()]),
    )
    assert result.websocket == 0.0
    assert result.outcomes[0].websocket is None
    full = format_run(result, verbose=True, color=False)
    assert "WebSocket  (" not in full
    assert "websocket=" not in full
    html = format_html(result)
    assert ">WebSocket</h2>" not in html
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["websocket_status"] == ""
    assert rows[0]["websocket_connect_ms"] == ""
    data = run_to_dict(result)
    assert "websocket" not in data
    assert "websocket" not in data["ranking"][0]


def test_websocket_not_mixed_into_ranking_and_skips_http_budget() -> None:
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return _ok(request)

    cfg = _cfg()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    plain = run_endpoints(cfg, samples=1, warmup=0, budget=64, client=client)
    used_plain = n["i"]
    n["i"] = 0
    measured = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=64,
        websocket=0.05,
        client=client,
        open_ws=_open([_ack(), _head(20)]),
    )
    assert n["i"] == used_plain
    assert plain.outcomes[0].stats.n_ok == measured.outcomes[0].stats.n_ok
    assert measured.outcomes[0].websocket is not None
    assert plain.outcomes[0].websocket is None


def test_websocket_connect_failure_is_connection() -> None:
    result = run_endpoints(
        _cfg(),
        samples=1,
        warmup=0,
        budget=32,
        websocket=0.05,
        client=_client(),
        open_ws=_open([], fail=OSError("refused")),
    )
    hit = result.outcomes[0].websocket
    assert hit is not None
    assert not hit.ok
    assert hit.error_class == "connection"
    assert hit.connect_ms is not None
    compact = format_run(result, color=False)
    table = compact.split("WebSocket", 1)[1]
    assert "connection" in table


def test_websocket_unsupported_subscribe_is_skip() -> None:
    err = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32601, "message": "Method not found"},
    }
    result = run_endpoints(
        _cfg(),
        samples=1,
        warmup=0,
        budget=32,
        websocket=0.05,
        client=_client(),
        open_ws=_open([err]),
    )
    hit = result.outcomes[0].websocket
    assert hit is not None
    assert hit.skip == "unsupported"
    assert websocket_label(hit) == "skip/unsupported"
    compact = format_run(result, color=False)
    assert "skip/unsupported" in compact.split("WebSocket", 1)[1]


def test_websocket_reuses_headers() -> None:
    opener = _open([_ack()])
    cfg = _cfg(headers={"Authorization": "Bearer tok_secret"})
    run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=32,
        websocket=0.05,
        client=_client(),
        open_ws=opener,
    )
    assert ("Authorization", "Bearer tok_secret") in opener.captured["headers"]
    assert opener.captured["url"] == "ws://127.0.0.1:8546"


def test_probe_websocket_family_skip() -> None:
    ep = parse_endpoints(
        {"endpoints": [{"name": "x", "url": "http://127.0.0.1:1", "ws": "ws://127.0.0.1:2"}]}
    ).endpoints[0]
    hit = probe_websocket(
        ep, window=0.05, timeout=1.0, family="aptos", open_ws=_open([_ack()])
    )
    assert hit.skip == "family"
    assert websocket_label(hit) == "skip/family"


def test_probe_websocket_cosmos_new_block() -> None:
    ep = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "x",
                    "url": "http://127.0.0.1:1",
                    "ws": "ws://127.0.0.1:2",
                    "family": "cosmos",
                }
            ]
        }
    ).endpoints[0]
    head = {
        "jsonrpc": "2.0",
        "id": 0,
        "result": {
            "query": "tm.event='NewBlock'",
            "data": {
                "type": "tendermint/event/NewBlock",
                "value": {
                    "block": {
                        "header": {
                            "height": "42",
                            "chain_id": "cosmoshub-4",
                        }
                    }
                },
            },
        },
    }
    sock = ScriptedWS([_ack({}), head])

    def open_ws(url: str, *, headers=(), timeout: float = 10.0):
        return sock

    hit = probe_websocket(
        ep, window=0.05, timeout=1.0, family="cosmos", open_ws=open_ws
    )
    assert hit.ok
    assert hit.n_events >= 1
    sent = json.loads(sock.sent[0])
    assert sent["method"] == "subscribe"
    assert sent["params"] == ["tm.event='NewBlock'"]


def test_probe_websocket_substrate_new_heads() -> None:
    ep = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "x",
                    "url": "http://127.0.0.1:1",
                    "ws": "ws://127.0.0.1:2",
                    "family": "substrate",
                }
            ]
        }
    ).endpoints[0]
    head = {
        "jsonrpc": "2.0",
        "method": "chain_newHead",
        "params": {
            "subscription": "sub1",
            "result": {
                "parentHash": "0x" + "11" * 32,
                "number": "0x10",
                "stateRoot": "0x" + "22" * 32,
                "extrinsicsRoot": "0x" + "33" * 32,
                "digest": {"logs": []},
            },
        },
    }
    sock = ScriptedWS([_ack("sub1"), head])

    def open_ws(url: str, *, headers=(), timeout: float = 10.0):
        return sock

    hit = probe_websocket(
        ep, window=0.05, timeout=1.0, family="substrate", open_ws=open_ws
    )
    assert hit.ok
    assert hit.n_events >= 1
    assert json.loads(sock.sent[0])["method"] == "chain_subscribeNewHeads"


def test_probe_websocket_solana_slot_subscribe() -> None:
    ep = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "x",
                    "url": "http://127.0.0.1:1",
                    "ws": "ws://127.0.0.1:2",
                    "family": "solana",
                }
            ]
        }
    ).endpoints[0]
    slot = {
        "jsonrpc": "2.0",
        "method": "slotNotification",
        "params": {"subscription": 1, "result": {"slot": 10, "parent": 9, "root": 1}},
    }
    sock = ScriptedWS([_ack(1), slot])

    def open_ws(url: str, *, headers=(), timeout: float = 10.0):
        return sock

    hit = probe_websocket(
        ep, window=0.05, timeout=1.0, family="solana", open_ws=open_ws
    )
    assert hit.ok
    assert hit.n_events >= 1
    assert json.loads(sock.sent[0])["method"] == "slotSubscribe"


def test_websocket_table_is_in_compact_cli() -> None:
    result = run_endpoints(
        _cfg(),
        samples=1,
        warmup=0,
        budget=32,
        websocket=0.05,
        client=_client(),
        open_ws=_open([_ack(), _head(1)]),
    )
    compact = format_run(result, verbose=False, color=False)
    assert "WebSocket  (" in compact
    assert "Providers" not in compact
