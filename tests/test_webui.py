"""Localhost live web UI (--web / rpcbench ui)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

import httpx
import pytest

from rpcbench.cli import build_parser, main
from rpcbench.rpc import ProbeResult
from rpcbench.webui import (
    LiveWebUi,
    WebHostError,
    normalize_web_host,
    should_wait_web,
    wants_web,
)


def test_wants_web() -> None:
    assert wants_web(web=True) is True
    assert wants_web(web=False) is False


def test_should_wait_web_respects_ci() -> None:
    class Tty:
        def isatty(self) -> bool:
            return True

    assert should_wait_web(ci=True, stdin=Tty()) is False
    assert should_wait_web(ci=False, stdin=Tty()) is True


def test_normalize_web_host() -> None:
    assert normalize_web_host("localhost") == "127.0.0.1"
    assert normalize_web_host("0.0.0.0") == "0.0.0.0"
    assert normalize_web_host("192.168.1.10") == "192.168.1.10"
    with pytest.raises(WebHostError):
        normalize_web_host("  ")


def test_public_bind_allowed(monkeypatch) -> None:
    monkeypatch.setattr("rpcbench.webui.webbrowser.open", lambda url: True)
    ui = LiveWebUi(["a"], host="0.0.0.0", port=0, open_browser=False)
    url = ui.start()
    assert url is not None
    assert ui.host == "0.0.0.0"
    # Browse URL uses loopback when bound on all interfaces.
    assert url.startswith("http://127.0.0.1:")
    ui.stop()


def test_cli_web_host_public(tmp_path: Path, monkeypatch, capsys) -> None:
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
    monkeypatch.setattr("rpcbench.webui.webbrowser.open", lambda url: True)
    monkeypatch.setattr("rpcbench.cli.wants_tui", lambda **kwargs: False)
    monkeypatch.setattr("rpcbench.cli.should_wait_web", lambda **kwargs: False)

    seen: list[str] = []

    class Tracking(LiveWebUi):
        def start(self):
            seen.append(self.host)
            return super().start()

    monkeypatch.setattr("rpcbench.cli.LiveWebUi", Tracking)
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
            "--web",
            "--web-host",
            "0.0.0.0",
            "--web-port",
            "0",
        ]
    )
    assert code == 0
    assert seen == ["0.0.0.0"]
    assert "no auth" in capsys.readouterr().err


def test_live_api_and_stop(monkeypatch) -> None:
    monkeypatch.setattr("rpcbench.webui.webbrowser.open", lambda url: True)
    ui = LiveWebUi(["a", "b"], port=0, open_browser=False)
    url = ui.start()
    assert url is not None
    assert url.startswith("http://127.0.0.1:")
    try:
        ui.record(
            "a",
            ProbeResult(
                ok=True,
                reachable=True,
                latency_ms=12.5,
                result="0x10",
                error=None,
                error_class=None,
                attempts=1,
            ),
            kind="sample",
        )
        ui.record(
            "b",
            ProbeResult(
                ok=False,
                reachable=True,
                latency_ms=None,
                result=None,
                error="timeout",
                error_class="timeout",
                attempts=1,
            ),
            kind="sample",
        )
        with urlopen(url + "api/live", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["providers"][0]["name"] == "a"
        assert data["providers"][0]["p50"] == 12.5
        assert data["providers"][0]["height"] == 16
        assert data["providers"][1]["n_fail"] == 1
        assert data["totals"]["n"] == 2

        req = Request(url + "api/stop", data=b"", method="POST")
        with urlopen(req, timeout=2) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["ok"] is True
        assert ui.should_abort() is True

        ui.set_report("<html>report</html>")
        ui.finish(aborted=True)
        with urlopen(url + "report", timeout=2) as resp:
            assert b"report" in resp.read()
        with urlopen(url, timeout=2) as resp:
            page = resp.read().decode("utf-8")
        assert "rpcbench live" in page
        assert "Stop" in page
    finally:
        ui.stop()


def test_parser_web_and_ui() -> None:
    ns = build_parser().parse_args(
        [
            "compare",
            "--endpoints",
            "x.yaml",
            "--web",
            "--web-host",
            "0.0.0.0",
            "--web-port",
            "0",
        ]
    )
    assert ns.web is True
    assert ns.web_host == "0.0.0.0"
    assert ns.web_port == 0
    ui = build_parser().parse_args(["ui", "--endpoints", "x.yaml"])
    assert ui.web is True
    assert ui.command == "ui"
    assert ui.web_host == "127.0.0.1"


def test_help_lists_web() -> None:
    with pytest.raises(SystemExit):
        main(["compare", "--help"])
    # argparse writes help to stdout via SystemExit — use parser format
    text = build_parser().format_help()
    assert "--web" in text
    assert "ui" in text


def test_cli_web_stop_writes_reports(tmp_path: Path, monkeypatch, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    held: dict[str, LiveWebUi] = {}

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
    monkeypatch.setattr("rpcbench.webui.webbrowser.open", lambda url: True)
    monkeypatch.setattr("rpcbench.cli.wants_tui", lambda **kwargs: False)
    monkeypatch.setattr("rpcbench.cli.should_wait_web", lambda **kwargs: False)

    class Tracking(LiveWebUi):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            held["ui"] = self

    monkeypatch.setattr("rpcbench.cli.LiveWebUi", Tracking)

    real_run = __import__("rpcbench.run", fromlist=["run_endpoints"]).run_endpoints

    def wrapped(*args, **kwargs):
        original = kwargs.get("on_sample")
        hits = {"n": 0}

        def counting(name, hit, kind):
            hits["n"] += 1
            if original:
                original(name, hit, kind)
            if hits["n"] == 2:
                held["ui"].request_abort()

        kwargs["on_sample"] = counting
        return real_run(*args, **kwargs)

    monkeypatch.setattr("rpcbench.cli.run_endpoints", wrapped)

    code = main(
        [
            "ui",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_blockNumber",
            "--samples",
            "20",
            "--warmup",
            "0",
            "--plain",
            "--web-port",
            "0",
            "--out-dir",
            str(out),
        ]
    )
    assert code in (0, 1)
    err = capsys.readouterr().err
    assert "web UI http://127.0.0.1:" in err
    assert (out / "report.html").exists()
    assert (out / "report.json").exists()
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data.get("aborted") is True


def test_cli_web_binds_loopback_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
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
    monkeypatch.setattr("rpcbench.webui.webbrowser.open", lambda url: True)
    monkeypatch.setattr("rpcbench.cli.wants_tui", lambda **kwargs: False)
    monkeypatch.setattr("rpcbench.cli.should_wait_web", lambda **kwargs: False)

    seen: list[str] = []

    class Tracking(LiveWebUi):
        def start(self):
            seen.append(self.host)
            return super().start()

    monkeypatch.setattr("rpcbench.cli.LiveWebUi", Tracking)
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
            "--web",
            "--web-port",
            "0",
        ]
    )
    assert code == 0
    assert seen == ["127.0.0.1"]
