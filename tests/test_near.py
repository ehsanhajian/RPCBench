"""NEAR JSON-RPC family benchmark mix. No live public RPCs."""

from __future__ import annotations

import json

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.consistency import parse_block_hash
from rpcbench.family import NEAR, pin_block_params, resolve_block_time
from rpcbench.freshness import parse_block_height
from rpcbench.logs import family_skip_reason
from rpcbench.methods import (
    FAMILY_NEAR,
    MethodError,
    family_workload,
    resolve_workload,
)
from rpcbench.report import format_run
from rpcbench.rpc import NamedParams, encode_rpc_params
from rpcbench.run import run_endpoints
from rpcbench.tags import parse_client_label

_HASH = "EPnLgE7iEq9s7yTkos96M3cWymH5avBAPm3qx3NXqR8H"
_STATUS = {
    "chain_id": "mainnet",
    "genesis_hash": _HASH,
    "protocol_version": 86,
    "sync_info": {
        "latest_block_hash": _HASH,
        "latest_block_height": 100,
        "syncing": False,
    },
    "version": {"version": "2.0.0", "build": "test"},
}
_BLOCK = {
    "author": "pool.near",
    "header": {
        "height": 100,
        "hash": _HASH,
        "prev_hash": "11111111111111111111111111111111",
    },
    "chunks": [],
}


def _near_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload.get("method")
    ident = payload.get("id", 1)
    params = payload.get("params")
    if method == "status":
        result: object = _STATUS
    elif method == "block":
        assert isinstance(params, dict)
        result = _BLOCK
    elif method == "gas_price":
        result = {"gas_price": "100000000"}
    elif method == "query":
        assert isinstance(params, dict)
        result = {
            "amount": "0",
            "block_hash": _HASH,
            "block_height": 100,
            "code_hash": _HASH,
            "locked": "0",
            "storage_usage": 0,
        }
    elif method == "network_info":
        result = {"active_peers": [], "num_active_peers": 0}
    elif method == "validators":
        result = {"current_validators": [], "current_proposals": []}
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
                    "url": "http://127.0.0.1:3030/a",
                    "family": "near",
                },
                {
                    "name": "b",
                    "url": "http://127.0.0.1:3030/b",
                    "family": "near",
                },
            ]
        }
    )


def test_near_adapter_and_named_params() -> None:
    assert NEAR.head_method == "status"
    assert NEAR.ws_method == ""
    assert pin_block_params(NEAR, 100) == [NamedParams(block_id=100)]
    assert encode_rpc_params([NamedParams(finality="final")]) == {
        "finality": "final"
    }
    assert resolve_block_time(NEAR, chain_id=None, override=None) == 1.2
    assert parse_block_height(_STATUS) == 100
    assert parse_block_height(_BLOCK) == 100
    assert parse_block_hash(_BLOCK) == _HASH
    assert parse_block_hash(_STATUS) == _HASH
    assert parse_client_label(_STATUS) == "near 2.0.0"


def test_near_workloads_use_near_methods() -> None:
    for name in ("general", "wallet", "indexer", "trading", "nft"):
        steps = family_workload(FAMILY_NEAR, name)
        methods = {spec.method for spec in steps}
        assert "status" in methods
        assert all(not m.startswith("eth_") for m in methods)
        assert all(not m.startswith("trace_") for m in methods)
        assert all(not m.startswith("debug_") for m in methods)
    with pytest.raises(MethodError, match="no near mix"):
        family_workload(FAMILY_NEAR, "tracing")


def test_compare_two_near_endpoints_mock() -> None:
    plan = resolve_workload(
        profile=None,
        workload="general",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_NEAR,
    )
    client = httpx.Client(transport=httpx.MockTransport(_near_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="general",
        family=FAMILY_NEAR,
        client=client,
    )
    assert result.family == "near"
    assert result.block_time_s == 1.2
    assert len(result.outcomes) == 2
    for outcome in result.outcomes:
        assert outcome.stats.n_ok >= 1
        methods = {hit.method for hit in outcome.samples if hit.method}
        assert "status" in methods
        assert "eth_blockNumber" not in methods
        assert outcome.freshness is not None
        assert outcome.freshness.height == 100
        assert outcome.consistency is not None
        assert outcome.consistency.hash == _HASH
        assert outcome.client == "near 2.0.0"
    text = format_run(result, color=False)
    assert "general" in text
    assert "head" in text
    assert "eth_blockNumber" not in text


def test_evm_only_extras_skip_on_near() -> None:
    assert family_skip_reason("near") == "family"
    plan = resolve_workload(
        profile=None,
        workload="wallet",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_NEAR,
    )
    client = httpx.Client(transport=httpx.MockTransport(_near_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="wallet",
        family=FAMILY_NEAR,
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


def test_cli_near_compare(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import cli
    from rpcbench import run as run_mod

    cfg = tmp_path / "near.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:3030/a\n    family: near\n"
        "  - name: b\n    url: http://127.0.0.1:3030/b\n    family: near\n",
        encoding="utf-8",
    )
    real = run_mod.run_endpoints

    def wrapped(*args, **kwargs):
        kwargs["client"] = httpx.Client(
            transport=httpx.MockTransport(_near_handler)
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = cli.main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--family",
            "near",
            "--method",
            "status",
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
