from __future__ import annotations

import csv
import io
import json

import httpx

from rpcbench.archive import (
    ARCHIVE_BLOCK,
    MIN_HEAD,
    archive_block,
    archive_params,
    archive_status,
    is_pruned,
)
from rpcbench.config import parse_endpoints
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.markdown import format_md
from rpcbench.methods import MIX_PROFILE, ZERO_ADDRESS
from rpcbench.report import format_run, run_to_dict
from rpcbench.run import run_endpoints


def _cfg(*names: str):
    endpoints = [
        {"name": name, "url": f"http://127.0.0.1:8545/{name}"} for name in names
    ]
    return parse_endpoints({"endpoints": endpoints})


def _handler(kind: str):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)}
            )
        if method == "eth_getBalance":
            params = payload.get("params") or []
            tag = params[1] if len(params) > 1 else None
            if tag == hex(ARCHIVE_BLOCK):
                if kind == "yes":
                    return httpx.Response(
                        200, json={"jsonrpc": "2.0", "id": 1, "result": "0x0"}
                    )
                if kind == "no":
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
                if kind == "rate":
                    return httpx.Response(429, text="Too Many Requests")
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32000, "message": "execution reverted"},
                    },
                )
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
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

    return handler


def test_archive_window_needs_a_tall_head() -> None:
    assert archive_block(None) is None
    assert archive_block(MIN_HEAD - 1) is None
    assert archive_block(MIN_HEAD) == ARCHIVE_BLOCK
    assert archive_params() == (ZERO_ADDRESS, hex(ARCHIVE_BLOCK))
    assert is_pruned("missing trie node abc")
    assert is_pruned("historical state is not available")
    assert not is_pruned("method not found")
    assert not is_pruned(None)


def test_mix_balance_stays_latest() -> None:
    balance = next(spec for spec in MIX_PROFILE if spec.name == "balance")
    assert balance.params[1] == "latest"


def test_archive_yes_no_unknown_rate_limited() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        kind = {"ok": "yes", "pruned": "no", "weird": "unknown", "rl": "rate"}[name]
        return _handler(kind)(request)

    cfg = _cfg("ok", "pruned", "weird", "rl")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=64, client=client, archive=True
    )
    assert result.archive is True
    by_name = {o.endpoint.name: o for o in result.outcomes}
    assert archive_status(by_name["ok"].archive) == "yes"
    assert archive_status(by_name["pruned"].archive) == "no"
    assert archive_status(by_name["weird"].archive) == "unknown"
    assert archive_status(by_name["rl"].archive) == "rate_limited"
    assert by_name["ok"].stats.n_ok == 1
    text = format_run(result, color=False)
    assert "Archive" in text
    assert "yes" in text
    assert "no" in text
    assert "rate-limited" in text
    data = run_to_dict(result)
    assert data["archive"] is True
    statuses = {row["name"]: row["archive"]["status"] for row in data["ranking"]}
    assert statuses == {
        "ok": "yes",
        "pruned": "no",
        "weird": "unknown",
        "rl": "rate_limited",
    }
    html = format_html(result)
    md = format_md(result)
    csv_rows = {r["name"]: r for r in csv.DictReader(io.StringIO(format_csv(result)))}
    assert "Archive" in html
    assert "## Archive" in md
    assert csv_rows["ok"]["archive"] == "yes"
    assert csv_rows["pruned"]["archive"] == "no"
    assert csv_rows["pruned"]["archive_status"] == "no"
    assert csv_rows["rl"]["archive"] == "rate_limited"
    assert "finding" not in text.lower()
    assert "finding" not in html.lower()
    assert "finding" not in md.lower()
    assert "vuln" not in text.lower()


def test_missing_archive_is_capability_not_crash() -> None:
    result = run_endpoints(
        _cfg("pruned"),
        samples=1,
        warmup=0,
        budget=32,
        client=httpx.Client(transport=httpx.MockTransport(_handler("no"))),
        archive=True,
    )
    outcome = result.outcomes[0]
    assert archive_status(outcome.archive) == "no"
    assert outcome.stats.n_ok == 1
    data = run_to_dict(result)
    assert data["ranking"][0]["rank"] == 1
    assert data["ranking"][0]["reliable"] is True
    assert data["ranking"][0]["archive"]["status"] == "no"
    assert "finding" not in format_run(result, color=False).lower()


def test_short_head_skips_without_sending_genesis() -> None:
    seen: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_getBalance":
            seen.append((method, payload.get("params")))
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(50)}
            )
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

    result = run_endpoints(
        _cfg("short"),
        samples=1,
        warmup=0,
        budget=32,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        archive=True,
    )
    assert result.outcomes[0].archive.skip == "head"
    assert archive_status(result.outcomes[0].archive) == "unknown"
    assert seen == []
    text = format_run(result, color=False)
    assert "skip/head" in text


def test_without_flag_does_not_probe_genesis() -> None:
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "eth_getBalance":
            seen.append(payload.get("params"))
        if payload["method"] == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)}
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    result = run_endpoints(
        _cfg("ok"),
        samples=1,
        warmup=0,
        budget=32,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert result.archive is False
    assert result.outcomes[0].archive is None
    assert not any(
        isinstance(params, list) and len(params) > 1 and params[1] == hex(ARCHIVE_BLOCK)
        for params in seen
    )


def test_cli_archive(tmp_path, monkeypatch, capsys) -> None:
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
        if payload["method"] == "eth_getBalance":
            tags.append((payload.get("params") or [None, None])[1])
        return _handler("yes")(request)

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
            "--archive",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Archive" in out
    assert hex(ARCHIVE_BLOCK) in tags
    assert "finding" not in out.lower()
