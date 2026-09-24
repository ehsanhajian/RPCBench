"""Substrate family benchmark mix. No live public RPCs."""

from __future__ import annotations

import json

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.consistency import parse_block_hash
from rpcbench.family import SUBSTRATE, pin_block_params, resolve_block_time
from rpcbench.freshness import parse_block_height
from rpcbench.logs import family_skip_reason
from rpcbench.methods import (
    FAMILY_SUBSTRATE,
    MethodError,
    family_workload,
    resolve_workload,
)
from rpcbench.report import format_run
from rpcbench.run import run_endpoints

_HASH = "0x" + "ab" * 32
_HEADER = {
    "parentHash": "0x" + "11" * 32,
    "number": "0x64",
    "stateRoot": "0x" + "22" * 32,
    "extrinsicsRoot": "0x" + "33" * 32,
    "digest": {"logs": []},
}


def _substrate_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload.get("method")
    ident = payload.get("id", 1)
    if method == "chain_getHeader":
        result: object = _HEADER
    elif method == "system_chain":
        result = "Polkadot"
    elif method == "chain_getBlockHash":
        result = _HASH
    elif method == "state_getRuntimeVersion":
        result = {"specName": "polkadot", "specVersion": 1, "implName": "parity"}
    elif method == "system_health":
        result = {"peers": 2, "isSyncing": False, "shouldHavePeers": True}
    elif method == "system_syncState":
        result = {"startingBlock": 0, "currentBlock": 100, "highestBlock": 100}
    elif method == "system_version":
        result = "parity-polkadot/1.0.0"
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
                    "url": "http://127.0.0.1:9933/a",
                    "family": "substrate",
                },
                {
                    "name": "b",
                    "url": "http://127.0.0.1:9933/b",
                    "family": "substrate",
                },
            ]
        }
    )


def test_substrate_adapter_and_pin_params() -> None:
    assert SUBSTRATE.head_method == "chain_getHeader"
    assert pin_block_params(SUBSTRATE, 100) == ["0x64"]
    assert resolve_block_time(SUBSTRATE, chain_id=None, override=None) == 6.0
    assert parse_block_height(_HEADER) == 100
    assert parse_block_hash(_HASH) == _HASH


def test_substrate_workloads_use_substrate_methods() -> None:
    for name in ("general", "wallet", "indexer", "trading", "nft"):
        steps = family_workload(FAMILY_SUBSTRATE, name)
        methods = {spec.method for spec in steps}
        assert "chain_getHeader" in methods
        assert all(not m.startswith("eth_") for m in methods)
        assert all(not m.startswith("trace_") for m in methods)
        assert all(not m.startswith("debug_") for m in methods)
    with pytest.raises(MethodError, match="no substrate mix"):
        family_workload(FAMILY_SUBSTRATE, "tracing")


def test_compare_two_substrate_endpoints_mock() -> None:
    plan = resolve_workload(
        profile=None,
        workload="general",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_SUBSTRATE,
    )
    client = httpx.Client(transport=httpx.MockTransport(_substrate_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="general",
        family=FAMILY_SUBSTRATE,
        client=client,
    )
    assert result.family == "substrate"
    assert result.block_time_s == 6.0
    assert len(result.outcomes) == 2
    for outcome in result.outcomes:
        assert outcome.stats.n_ok >= 1
        methods = {hit.method for hit in outcome.samples if hit.method}
        assert "chain_getHeader" in methods
        assert "eth_blockNumber" not in methods
        assert outcome.freshness is not None
        assert outcome.freshness.height == 100
        assert outcome.consistency is not None
        assert outcome.consistency.hash == _HASH
    text = format_run(result, color=False)
    assert "general" in text
    assert "head" in text
    assert "eth_blockNumber" not in text


def test_evm_only_extras_skip_on_substrate() -> None:
    assert family_skip_reason("substrate") == "family"
    plan = resolve_workload(
        profile=None,
        workload="wallet",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_SUBSTRATE,
    )
    client = httpx.Client(transport=httpx.MockTransport(_substrate_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="wallet",
        family=FAMILY_SUBSTRATE,
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


def test_cli_substrate_compare(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import cli
    from rpcbench import run as run_mod

    cfg = tmp_path / "dot.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:9933/a\n    family: substrate\n"
        "  - name: b\n    url: http://127.0.0.1:9933/b\n    family: substrate\n",
        encoding="utf-8",
    )
    real = run_mod.run_endpoints

    def wrapped(*args, **kwargs):
        kwargs["client"] = httpx.Client(
            transport=httpx.MockTransport(_substrate_handler)
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = cli.main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--family",
            "substrate",
            "--method",
            "chain_getHeader",
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
