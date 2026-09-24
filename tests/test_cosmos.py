"""Cosmos / CometBFT family benchmark mix. No live public RPCs."""

from __future__ import annotations

import json

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.consistency import parse_block_hash
from rpcbench.family import COSMOS, pin_block_params, resolve_block_time
from rpcbench.freshness import parse_block_height
from rpcbench.logs import family_skip_reason
from rpcbench.methods import (
    FAMILY_COSMOS,
    MethodError,
    family_workload,
    resolve_workload,
)
from rpcbench.report import format_run
from rpcbench.run import run_endpoints
from rpcbench.tags import parse_client_label

_HASH = "0x" + "ab" * 32
_STATUS = {
    "node_info": {"network": "cosmoshub-4", "version": "0.38.0"},
    "sync_info": {
        "latest_block_hash": "AB" * 32,
        "latest_block_height": "100",
        "catching_up": False,
    },
}
_BLOCK = {
    "block_id": {"hash": "AB" * 32},
    "block": {
        "header": {
            "height": "100",
            "chain_id": "cosmoshub-4",
        }
    },
}
_ABCI = {
    "response": {
        "data": "GaiaApp",
        "version": "v28.0.0",
        "last_block_height": "100",
    }
}


def _cosmos_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload.get("method")
    ident = payload.get("id", 1)
    if method == "status":
        result: object = _STATUS
    elif method == "abci_info":
        result = _ABCI
    elif method == "block":
        result = _BLOCK
    elif method == "net_info":
        result = {"listening": True, "n_peers": "2", "peers": []}
    elif method == "num_unconfirmed_txs":
        result = {"n_txs": "0", "total": "0", "total_bytes": "0"}
    elif method == "consensus_state":
        result = {"round_state": {"height/round/step": "100/0/1"}}
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
                    "url": "http://127.0.0.1:26657/a",
                    "family": "cosmos",
                },
                {
                    "name": "b",
                    "url": "http://127.0.0.1:26657/b",
                    "family": "cosmos",
                },
            ]
        }
    )


def test_cosmos_adapter_and_pin_params() -> None:
    assert COSMOS.head_method == "status"
    assert pin_block_params(COSMOS, 100) == ["100"]
    assert resolve_block_time(COSMOS, chain_id=None, override=None) == 6.0
    assert parse_block_height(_STATUS) == 100
    assert parse_block_height(_BLOCK) == 100
    assert parse_block_hash(_STATUS) == _HASH
    assert parse_block_hash(_BLOCK) == _HASH
    assert parse_client_label(_ABCI) == "GaiaApp"


def test_cosmos_workloads_use_comet_methods() -> None:
    for name in ("general", "wallet", "indexer", "trading", "nft"):
        steps = family_workload(FAMILY_COSMOS, name)
        methods = {spec.method for spec in steps}
        assert "status" in methods
        assert all(not m.startswith("eth_") for m in methods)
        assert all(not m.startswith("trace_") for m in methods)
        assert all(not m.startswith("debug_") for m in methods)
    with pytest.raises(MethodError, match="no cosmos mix"):
        family_workload(FAMILY_COSMOS, "tracing")


def test_compare_two_cosmos_endpoints_mock() -> None:
    plan = resolve_workload(
        profile=None,
        workload="general",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_COSMOS,
    )
    client = httpx.Client(transport=httpx.MockTransport(_cosmos_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="general",
        family=FAMILY_COSMOS,
        client=client,
    )
    assert result.family == "cosmos"
    assert result.block_time_s == 6.0
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
    text = format_run(result, color=False)
    assert "general" in text
    assert "head" in text
    assert "eth_blockNumber" not in text


def test_evm_only_extras_skip_on_cosmos() -> None:
    assert family_skip_reason("cosmos") == "family"
    plan = resolve_workload(
        profile=None,
        workload="wallet",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_COSMOS,
    )
    client = httpx.Client(transport=httpx.MockTransport(_cosmos_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="wallet",
        family=FAMILY_COSMOS,
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


def test_cli_cosmos_compare(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import cli
    from rpcbench import run as run_mod

    cfg = tmp_path / "atom.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:26657/a\n    family: cosmos\n"
        "  - name: b\n    url: http://127.0.0.1:26657/b\n    family: cosmos\n",
        encoding="utf-8",
    )
    real = run_mod.run_endpoints

    def wrapped(*args, **kwargs):
        kwargs["client"] = httpx.Client(
            transport=httpx.MockTransport(_cosmos_handler)
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = cli.main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--family",
            "cosmos",
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
