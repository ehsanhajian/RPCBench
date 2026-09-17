from __future__ import annotations

import csv
import io
import json

import httpx

from rpcbench.archive import ARCHIVE_BLOCK, MIN_HEAD, archive_status
from rpcbench.config import parse_endpoints
from rpcbench.csv import format_csv
from rpcbench.history import (
    DEFAULT_LOOKBACK,
    history_block,
    history_label,
    history_skip_reason,
    latest_params,
)
from rpcbench.html import format_html
from rpcbench.markdown import format_md
from rpcbench.methods import ZERO_ADDRESS
from rpcbench.report import format_run, run_to_dict
from rpcbench.run import run_endpoints


def _cfg(*names: str):
    endpoints = [
        {"name": name, "url": f"http://127.0.0.1:8545/{name}"} for name in names
    ]
    return parse_endpoints({"endpoints": endpoints})


def _ok_block(height: int = 1000) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"number": hex(height), "hash": "0xabc"},
        },
    )


def test_history_block_math() -> None:
    assert history_block(None, 1000) is None
    assert history_block(50, 1000) is None
    assert history_block(1000, 1000) == 0
    assert history_block(1000, 100) == 900
    assert DEFAULT_LOOKBACK == 1000
    assert latest_params()[0] == ZERO_ADDRESS
    assert latest_params()[1] == "latest"


def test_history_skip_reuses_archive_no() -> None:
    from rpcbench.archive import skipped_archive
    from rpcbench.archive import hit_from_probe
    from rpcbench.rpc import ProbeResult

    pruned = hit_from_probe(
        ProbeResult(
            ok=False,
            reachable=True,
            latency_ms=5.0,
            result=None,
            error="missing trie node",
            error_class="jsonrpc",
            attempts=1,
        )
    )
    assert archive_status(pruned) == "no"
    assert (
        history_skip_reason(
            pin=1000, lookback=1000, family="evm", archive=pruned
        )
        == "archive"
    )
    assert (
        history_skip_reason(
            pin=1000, lookback=50, family="evm", archive=pruned
        )
        is None
    )
    assert history_skip_reason(
        pin=50, lookback=1000, family="evm", archive=skipped_archive("head")
    ) == "head"


def test_historical_latency_vs_head() -> None:
    seen: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        params = payload.get("params")
        if method == "eth_getBalance":
            seen.append((method, params[1] if params else None))
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)}
            )
        if method == "eth_getBalance":
            tag = params[1]
            if tag == "latest":
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
                )
            if tag == hex(900):
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2"}
                )
            if tag == hex(ARCHIVE_BLOCK):
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": 1, "result": "0x0"}
                )
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "0x3"}
            )
        if method == "eth_getBlockByNumber":
            return _ok_block(1000)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    result = run_endpoints(
        _cfg("ok"),
        samples=1,
        warmup=0,
        budget=64,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        archive=True,
        lookback=100,
    )
    assert result.lookback == 100
    hit = result.outcomes[0].history
    assert hit is not None
    assert hit.block == 900
    assert hit.ok
    assert hit.latency_ms is not None
    assert hit.head_ms is not None
    assert hit.ratio is not None
    assert hit.n_ok == 1
    assert hit.n_fail == 0
    assert history_label(hit) == "ok"
    assert result.outcomes[0].stats.n_ok == 1
    tags = [tag for _m, tag in seen]
    assert "latest" in tags
    assert hex(900) in tags
    text = format_run(result, color=False)
    assert "History" in text
    assert "900" in text
    data = run_to_dict(result)
    assert data["lookback"] == 100
    row = data["ranking"][0]["history"]
    assert row["block"] == 900
    assert row["ok"] is True
    assert row["error_rate"] == 0.0
    html = format_html(result)
    md = format_md(result)
    csv_row = next(csv.DictReader(io.StringIO(format_csv(result))))
    assert "History" in html
    assert "## History" in md
    assert csv_row["history_status"] == "ok"
    assert csv_row["history_block"] == "900"
    assert "finding" not in text.lower()
    assert "finding" not in html.lower()
    assert "finding" not in md.lower()


def test_missing_archive_skips_historical() -> None:
    hist_tags: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        params = payload.get("params") or []
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)}
            )
        if method == "eth_getBalance":
            tag = params[1] if len(params) > 1 else None
            hist_tags.append(tag)
            if tag == hex(ARCHIVE_BLOCK):
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {
                            "code": -32000,
                            "message": "missing trie node",
                        },
                    },
                )
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
            )
        if method == "eth_getBlockByNumber":
            return _ok_block(1000)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    result = run_endpoints(
        _cfg("pruned"),
        samples=1,
        warmup=0,
        budget=32,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        archive=True,
        lookback=1000,
    )
    hit = result.outcomes[0].history
    assert hit is not None
    assert hit.skip == "archive"
    assert history_label(hit) == "skip/archive"
    assert hit.n_ok == 0
    assert hex(0) in hist_tags  # archive genesis
    assert "latest" not in hist_tags
    data = run_to_dict(result)
    assert data["ranking"][0]["history"]["status"] == "skip/archive"
    assert data["ranking"][0]["history"]["error_rate"] is None
    assert data["ranking"][0]["rank"] == 1
    text = format_run(result, color=False)
    assert "skip/archive" in text
    assert "finding" not in text.lower()


def test_shallow_lookback_still_runs_on_pruned() -> None:
    tags: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        params = payload.get("params") or []
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)}
            )
        if method == "eth_getBalance":
            tag = params[1] if len(params) > 1 else None
            tags.append(tag)
            if tag == hex(ARCHIVE_BLOCK):
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32000, "message": "missing trie node"},
                    },
                )
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
            )
        if method == "eth_getBlockByNumber":
            return _ok_block(1000)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    result = run_endpoints(
        _cfg("pruned"),
        samples=1,
        warmup=0,
        budget=32,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        archive=True,
        lookback=50,
    )
    assert result.outcomes[0].history.ok
    assert result.outcomes[0].history.block == 950
    assert hex(950) in tags
    assert "latest" in tags
    assert 50 < MIN_HEAD


def test_cli_lookback(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import run as run_mod
    from rpcbench.cli import main

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    tags: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(2000)}
            )
        if payload["method"] == "eth_getBalance":
            tags.append((payload.get("params") or [None, None])[1])
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "0x0"}
            )
        if payload["method"] == "eth_getBlockByNumber":
            return _ok_block(2000)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--lookback",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "History" in out
    assert "Archive" in out
    assert hex(ARCHIVE_BLOCK) in tags
    assert "latest" in tags
    assert hex(2000 - DEFAULT_LOOKBACK) in tags
    assert "finding" not in out.lower()
