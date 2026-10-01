from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest

from rpcbench.cli import main
from rpcbench.rpc import ProbeResult
from rpcbench.tui import LiveTui, ProviderLive, wants_tui


def test_wants_tui_respects_plain_and_ci() -> None:
    class Fake:
        def isatty(self) -> bool:
            return True

    assert wants_tui(plain=False, ci=False, stdout=Fake()) is True
    assert wants_tui(plain=True, ci=False, stdout=Fake()) is False
    assert wants_tui(plain=False, ci=True, stdout=Fake()) is False

    class Pipe:
        def isatty(self) -> bool:
            return False

    assert wants_tui(plain=False, ci=False, stdout=Pipe()) is False


def test_live_tui_updates_per_provider_stats() -> None:
    buf = io.StringIO()
    live = LiveTui(["a", "b"], stream=buf, enabled=True)
    live.record(
        "a",
        ProbeResult(
            ok=True,
            reachable=True,
            latency_ms=10.0,
            result="0x1",
            error=None,
            error_class=None,
            attempts=1,
        ),
        kind="sample",
    )
    live.record(
        "a",
        ProbeResult(
            ok=True,
            reachable=True,
            latency_ms=20.0,
            result="0x1",
            error=None,
            error_class=None,
            attempts=1,
        ),
        kind="sample",
    )
    live.record(
        "b",
        ProbeResult(
            ok=False,
            reachable=False,
            latency_ms=5.0,
            result=None,
            error="timeout",
            error_class="timeout",
            attempts=1,
        ),
        kind="sample",
    )
    live.finish()
    text = buf.getvalue()
    assert "provider" in text
    assert "a" in text and "b" in text
    assert live.providers["a"].n_ok == 2
    assert live.providers["a"].p95() == 20.0
    assert live.providers["b"].n_fail == 1
    assert live.providers["a"].spark()


def test_sparkline_scales() -> None:
    row = ProviderLive(name="x", ok_ms=[1.0, 50.0, 100.0])
    spark = row.spark()
    assert len(spark) == 3
    assert spark[0] == "▁"
    assert spark[-1] == "█"


def test_cli_plain_disables_tui(tmp_path: Path, monkeypatch, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ident = payload.get("id", 1)
        method = payload.get("method")
        if method == "eth_chainId":
            result: object = "0x1"
        elif method == "eth_blockNumber":
            result = "0x10"
        elif method == "eth_getBlockByNumber":
            result = {
                "number": "0x10",
                "hash": "0x" + "11" * 32,
                "parentHash": "0x" + "22" * 32,
            }
        else:
            result = "0x0"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": ident, "result": result}
        )

    transport = httpx.MockTransport(handler)

    def fake_client(**kwargs):
        return httpx.Client(transport=transport, timeout=5.0)

    monkeypatch.setattr("rpcbench.run.make_client", fake_client)
    # Force TTY so --plain is what disables it
    monkeypatch.setattr("rpcbench.tui.wants_tui", lambda **kwargs: not kwargs.get("plain") and not kwargs.get("ci"))
    seen: list[bool] = []

    class TrackingTui(LiveTui):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.append(self.enabled)

    monkeypatch.setattr("rpcbench.cli.LiveTui", TrackingTui)
    code = main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_blockNumber",
            "--samples",
            "1",
            "--warmup",
            "0",
            "--plain",
        ]
    )
    assert code == 0
    assert seen == [False]


def test_cli_ci_disables_tui(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ident = payload.get("id", 1)
        method = payload.get("method")
        if method == "eth_chainId":
            result: object = "0x1"
        elif method == "eth_blockNumber":
            result = "0x10"
        elif method == "eth_getBlockByNumber":
            result = {
                "number": "0x10",
                "hash": "0x" + "11" * 32,
                "parentHash": "0x" + "22" * 32,
            }
        else:
            result = "0x0"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": ident, "result": result}
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "rpcbench.run.make_client",
        lambda **kwargs: httpx.Client(transport=transport, timeout=5.0),
    )
    seen: list[bool] = []

    class TrackingTui(LiveTui):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.append(self.enabled)

    monkeypatch.setattr("rpcbench.cli.LiveTui", TrackingTui)
    monkeypatch.setattr(
        "rpcbench.tui.wants_tui",
        lambda **kwargs: not kwargs.get("plain") and not kwargs.get("ci"),
    )
    code = main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_blockNumber",
            "--samples",
            "1",
            "--warmup",
            "0",
            "--ci",
            "--max-p95",
            "5000",
        ]
    )
    assert code == 0
    assert seen == [False]


def test_abort_flushes_json(tmp_path: Path, monkeypatch, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )
    out = tmp_path / "report.json"
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ident = payload.get("id", 1)
        method = payload.get("method")
        n["i"] += 1
        if method == "eth_chainId":
            result: object = "0x1"
        elif method == "eth_blockNumber":
            result = "0x10"
        elif method == "eth_getBlockByNumber":
            result = {
                "number": "0x10",
                "hash": "0x" + "11" * 32,
                "parentHash": "0x" + "22" * 32,
            }
        else:
            result = "0x0"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": ident, "result": result}
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "rpcbench.run.make_client",
        lambda **kwargs: httpx.Client(transport=transport, timeout=5.0),
    )

    # Abort after a few samples
    state = {"hits": 0}

    def should_abort() -> bool:
        return state["hits"] >= 3

    real_run = __import__("rpcbench.run", fromlist=["run_endpoints"]).run_endpoints

    def wrapped(*args, **kwargs):
        original = kwargs.get("on_sample")

        def counting(name, hit, kind):
            state["hits"] += 1
            if original:
                original(name, hit, kind)

        kwargs["on_sample"] = counting
        kwargs["should_abort"] = should_abort
        return real_run(*args, **kwargs)

    monkeypatch.setattr("rpcbench.cli.run_endpoints", wrapped)
    monkeypatch.setattr("rpcbench.cli.wants_tui", lambda **kwargs: False)
    code = main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_blockNumber",
            "--samples",
            "20",
            "--warmup",
            "0",
            "--plain",
            "--json",
            "-o",
            str(out),
        ]
    )
    assert code in (0, 1)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["tool"] == "rpcbench"
    assert data.get("aborted") is True
    assert data["ranking"]
