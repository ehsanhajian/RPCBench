"""Bitcoin JSON-RPC family benchmark mix. No live public RPCs."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from rpcbench.config import ConfigError, parse_endpoints
from rpcbench.consistency import parse_block_hash
from rpcbench.family import BITCOIN, pin_block_params, resolve_block_time
from rpcbench.freshness import parse_block_height
from rpcbench.logs import family_skip_reason
from rpcbench.methods import (
    BITCOIN_FIRST_TX,
    BITCOIN_GENESIS,
    FAMILY_BITCOIN,
    MethodError,
    family_workload,
    resolve_workload,
)
from rpcbench.report import format_run, run_to_dict
from rpcbench.run import run_endpoints
from rpcbench.tags import parse_client_label

_HASH = "00000000000000000000905bdf198180016269a5354707114f8866d836bfbb74"
_INFO = {
    "chain": "main",
    "blocks": 100,
    "headers": 100,
    "bestblockhash": _HASH,
    "difficulty": 1.0,
    "mediantime": 1,
    "verificationprogress": 1.0,
    "initialblockdownload": False,
    "chainwork": "00",
    "size_on_disk": 1,
    "pruned": False,
}
_NET = {
    "version": 290300,
    "subversion": "/Satoshi:29.3.0/",
    "protocolversion": 70016,
    "connections": 8,
}
_BLOCK = {
    "hash": BITCOIN_GENESIS,
    "confirmations": 100,
    "height": 0,
    "version": 1,
    "merkleroot": "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b",
    "time": 1231006505,
    "nonce": 2083236893,
    "bits": "1d00ffff",
    "difficulty": 1.0,
    "previousblockhash": "00" * 32,
    "tx": [BITCOIN_FIRST_TX],
}


def _btc_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload.get("method")
    ident = payload.get("id", 1)
    if method == "getblockchaininfo":
        result: object = _INFO
    elif method == "getblockcount":
        result = 100
    elif method == "getnetworkinfo":
        result = _NET
    elif method == "getbestblockhash":
        result = _HASH
    elif method == "getblockhash":
        result = _HASH
    elif method == "getblock":
        result = _BLOCK
    elif method == "getmempoolinfo":
        result = {"loaded": True, "size": 0, "bytes": 0}
    elif method == "getrawtransaction":
        result = "010000000100"
    else:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": ident,
                "error": {"code": -32601, "message": f"no {method}"},
            },
        )
    return httpx.Response(
        200, json={"jsonrpc": "2.0", "id": ident, "result": result}
    )


def _cfg() -> object:
    return parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "a",
                    "url": "http://127.0.0.1:8332/a",
                    "family": "bitcoin",
                },
                {
                    "name": "b",
                    "url": "http://127.0.0.1:8332/b",
                    "family": "bitcoin",
                },
            ]
        }
    )


def test_bitcoin_adapter_and_ten_minute_cadence() -> None:
    assert BITCOIN.head_method == "getblockchaininfo"
    assert BITCOIN.ws_method == ""
    assert BITCOIN.block_time_s == 600.0
    assert pin_block_params(BITCOIN, 100) == [100]
    assert resolve_block_time(BITCOIN, chain_id=None, override=None) == 600.0
    assert parse_block_height(_INFO) == 100
    assert parse_block_height(_BLOCK) == 0
    assert parse_block_hash(_INFO) == "0x" + _HASH
    assert parse_block_hash(_HASH) == "0x" + _HASH
    assert parse_client_label(_NET) == "/Satoshi:29.3.0/"


def test_bitcoin_workloads_use_bitcoin_methods() -> None:
    for name in ("general", "wallet", "indexer", "trading", "nft"):
        steps = family_workload(FAMILY_BITCOIN, name)
        methods = {spec.method for spec in steps}
        assert "getblockchaininfo" in methods
        assert all(not m.startswith("eth_") for m in methods)
        assert all(not m.startswith("trace_") for m in methods)
        assert all(not m.startswith("debug_") for m in methods)
    with pytest.raises(MethodError, match="no bitcoin mix"):
        family_workload(FAMILY_BITCOIN, "tracing")


def test_compare_two_bitcoin_endpoints_mock() -> None:
    plan = resolve_workload(
        profile=None,
        workload="general",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_BITCOIN,
    )
    client = httpx.Client(transport=httpx.MockTransport(_btc_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="general",
        family=FAMILY_BITCOIN,
        client=client,
    )
    assert result.family == "bitcoin"
    assert result.block_time_s == 600.0
    assert len(result.outcomes) == 2
    for outcome in result.outcomes:
        assert outcome.stats.n_ok >= 1
        methods = {hit.method for hit in outcome.samples if hit.method}
        assert "getblockchaininfo" in methods
        assert "eth_blockNumber" not in methods
        assert outcome.freshness is not None
        assert outcome.freshness.height == 100
        assert outcome.consistency is not None
        assert outcome.consistency.hash == "0x" + _HASH
        assert outcome.client == "/Satoshi:29.3.0/"
    text = format_run(result, color=False)
    assert "general" in text
    assert "head" in text
    assert "eth_blockNumber" not in text
    blob = json.dumps(run_to_dict(result))
    assert "Basic " not in blob
    assert "Authorization" not in blob


def test_evm_only_extras_skip_on_bitcoin() -> None:
    assert family_skip_reason("bitcoin") == "family"
    plan = resolve_workload(
        profile=None,
        workload="wallet",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_BITCOIN,
    )
    client = httpx.Client(transport=httpx.MockTransport(_btc_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="wallet",
        family=FAMILY_BITCOIN,
        logs_range=1000,
        archive=True,
        lookback=1000,
        client=client,
    )
    for outcome in result.outcomes:
        assert outcome.logs_range
        assert all(hit.skip == "family" for hit in outcome.logs_range)
        assert outcome.archive is not None and outcome.archive.skip == "family"
        assert outcome.history is not None and outcome.history.skip == "family"


def test_user_password_becomes_basic_auth_and_stays_out_of_reports(
    tmp_path,
) -> None:
    # Concatenate so scanners do not treat the fixture as a live credential.
    pwd = "fixture" + "-rpc-pass"
    cookie_pwd = "fixture" + "-cookie"
    cfg = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "local",
                    "url": "http://127.0.0.1:8332",
                    "family": "bitcoin",
                    "user": "rpcuser",
                    "password": pwd,
                }
            ]
        }
    )
    endpoint = cfg.endpoints[0]
    expected = "Basic " + base64.b64encode(f"rpcuser:{pwd}".encode()).decode()
    assert ("Authorization", expected) in endpoint.headers
    assert pwd not in endpoint.display_url
    assert "***" not in endpoint.display_url  # no userinfo in URL

    cookie = tmp_path / ".cookie"
    cookie.write_text(f"__cookie__:{cookie_pwd}\n", encoding="utf-8")
    cfg2 = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "cookie",
                    "url": "http://127.0.0.1:8332",
                    "family": "bitcoin",
                    "cookie": str(cookie),
                }
            ]
        }
    )
    expected2 = "Basic " + base64.b64encode(
        f"__cookie__:{cookie_pwd}".encode()
    ).decode()
    assert ("Authorization", expected2) in cfg2.endpoints[0].headers
    with pytest.raises(ConfigError, match="mutually exclusive"):
        parse_endpoints(
            {
                "endpoints": [
                    {
                        "name": "both",
                        "url": "http://127.0.0.1:8332",
                        "family": "bitcoin",
                        "user": "rpcuser",
                        "password": "x",
                        "cookie": str(cookie),
                    }
                ]
            }
        )


def test_cli_bitcoin_compare(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import cli
    from rpcbench import run as run_mod

    cfg = tmp_path / "btc.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:8332/a\n    family: bitcoin\n"
        "  - name: b\n    url: http://127.0.0.1:8332/b\n    family: bitcoin\n",
        encoding="utf-8",
    )
    real = run_mod.run_endpoints

    def wrapped(*args, **kwargs):
        kwargs["client"] = httpx.Client(
            transport=httpx.MockTransport(_btc_handler)
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = cli.main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--family",
            "bitcoin",
            "--method",
            "getblockchaininfo",
            "--samples",
            "1",
            "--warmup",
            "0",
            "--max-requests",
            "32",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "a" in out
    assert "Basic " not in out
