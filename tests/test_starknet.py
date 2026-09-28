"""Starknet JSON-RPC family benchmark mix. No live public RPCs."""

from __future__ import annotations

import json

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.consistency import parse_block_hash
from rpcbench.family import STARKNET, pin_block_params, resolve_block_time
from rpcbench.freshness import parse_block_height
from rpcbench.logs import family_skip_reason
from rpcbench.methods import (
    FAMILY_STARKNET,
    MethodError,
    family_workload,
    resolve_workload,
)
from rpcbench.report import format_run
from rpcbench.run import run_endpoints
from rpcbench.tags import parse_client_label

_HASH = "0x" + "ab" * 31 + "cd"
_BLOCK = {
    "status": "ACCEPTED_ON_L2",
    "block_hash": _HASH,
    "parent_hash": "0x" + "11" * 32,
    "block_number": 100,
    "new_root": "0x" + "22" * 32,
    "timestamp": 1,
    "sequencer_address": "0x1",
    "l1_gas_price": {"price_in_wei": "0x1"},
    "starknet_version": "0.13.0",
    "transactions": [],
}


def _starknet_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload.get("method")
    ident = payload.get("id", 1)
    if method == "starknet_blockNumber":
        result: object = 100
    elif method == "starknet_chainId":
        result = "0x534e5f4d41494e"
    elif method == "starknet_specVersion":
        result = "0.10.2"
    elif method == "starknet_getBlockWithTxHashes":
        result = _BLOCK
    elif method == "starknet_syncing":
        result = False
    elif method == "starknet_getClassHashAt":
        result = "0x" + "b4" * 32
    elif method == "starknet_getNonce":
        result = "0x0"
    elif method == "starknet_getStorageAt":
        result = "0x0"
    elif method == "starknet_getEvents":
        result = {"events": [], "continuation_token": None}
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
                    "url": "http://127.0.0.1:9545/a",
                    "family": "starknet",
                },
                {
                    "name": "b",
                    "url": "http://127.0.0.1:9545/b",
                    "family": "starknet",
                },
            ]
        }
    )


def test_starknet_adapter_and_pin_params() -> None:
    assert STARKNET.head_method == "starknet_blockNumber"
    assert STARKNET.ws_method == ""
    assert pin_block_params(STARKNET, 100) == [{"block_number": 100}]
    assert resolve_block_time(STARKNET, chain_id=None, override=None) == 5.0
    assert parse_block_height(100) == 100
    assert parse_block_height(_BLOCK) == 100
    assert parse_block_hash(_BLOCK) == _HASH
    assert parse_client_label("0.10.2") == "0.10.2"


def test_starknet_workloads_use_starknet_methods() -> None:
    for name in ("general", "wallet", "indexer", "trading", "nft"):
        steps = family_workload(FAMILY_STARKNET, name)
        methods = {spec.method for spec in steps}
        assert "starknet_blockNumber" in methods
        assert all(not m.startswith("eth_") for m in methods)
        assert all(not m.startswith("trace_") for m in methods)
        assert all(not m.startswith("debug_") for m in methods)
    with pytest.raises(MethodError, match="no starknet mix"):
        family_workload(FAMILY_STARKNET, "tracing")


def test_compare_two_starknet_endpoints_mock() -> None:
    plan = resolve_workload(
        profile=None,
        workload="general",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_STARKNET,
    )
    client = httpx.Client(transport=httpx.MockTransport(_starknet_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="general",
        family=FAMILY_STARKNET,
        client=client,
    )
    assert result.family == "starknet"
    assert result.block_time_s == 5.0
    assert len(result.outcomes) == 2
    for outcome in result.outcomes:
        assert outcome.stats.n_ok >= 1
        methods = {hit.method for hit in outcome.samples if hit.method}
        assert "starknet_blockNumber" in methods
        assert "eth_blockNumber" not in methods
        assert outcome.freshness is not None
        assert outcome.freshness.height == 100
        assert outcome.consistency is not None
        assert outcome.consistency.hash == _HASH
        assert outcome.client == "0.10.2"
    text = format_run(result, color=False)
    assert "general" in text
    assert "head" in text
    assert "eth_blockNumber" not in text


def test_evm_only_extras_skip_on_starknet() -> None:
    assert family_skip_reason("starknet") == "family"
    plan = resolve_workload(
        profile=None,
        workload="wallet",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_STARKNET,
    )
    client = httpx.Client(transport=httpx.MockTransport(_starknet_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="wallet",
        family=FAMILY_STARKNET,
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


def test_cli_starknet_compare(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import cli
    from rpcbench import run as run_mod

    cfg = tmp_path / "sn.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:9545/a\n    family: starknet\n"
        "  - name: b\n    url: http://127.0.0.1:9545/b\n    family: starknet\n",
        encoding="utf-8",
    )
    real = run_mod.run_endpoints

    def wrapped(*args, **kwargs):
        kwargs["client"] = httpx.Client(
            transport=httpx.MockTransport(_starknet_handler)
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = cli.main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--family",
            "starknet",
            "--method",
            "starknet_blockNumber",
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
