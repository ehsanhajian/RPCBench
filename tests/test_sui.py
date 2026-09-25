"""Sui JSON-RPC family benchmark mix. No live public RPCs."""

from __future__ import annotations

import json

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.consistency import parse_block_hash
from rpcbench.family import SUI, pin_block_params, resolve_block_time
from rpcbench.freshness import parse_block_height
from rpcbench.logs import family_skip_reason
from rpcbench.methods import (
    FAMILY_SUI,
    MethodError,
    family_workload,
    resolve_workload,
)
from rpcbench.report import format_run
from rpcbench.run import run_endpoints
from rpcbench.tags import parse_client_label

_DIGEST = "75bKZsAaTU1LbNfnocyeqc9sWfucnzLdPUi7on9Unxf1"
_CHECKPOINT = {
    "epoch": "1",
    "sequenceNumber": "100",
    "digest": _DIGEST,
    "networkTotalTransactions": "1000",
}


def _sui_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload.get("method")
    ident = payload.get("id", 1)
    if method == "sui_getLatestCheckpointSequenceNumber":
        result: object = "100"
    elif method == "sui_getChainIdentifier":
        result = "35834a8a"
    elif method == "sui_getCheckpoint":
        result = _CHECKPOINT
    elif method == "suix_getReferenceGasPrice":
        result = "100"
    elif method == "suix_getBalance":
        result = {"coinType": "0x2::sui::SUI", "totalBalance": "0"}
    elif method == "sui_getObject":
        result = {"data": {"objectId": "0x6", "type": "0x2::clock::Clock"}}
    elif method == "suix_queryTransactionBlocks":
        result = {"data": [{"digest": _DIGEST}]}
    elif method == "suix_queryEvents":
        result = {"data": [{"type": "0x2::coin::CoinMetadata"}]}
    elif method == "suix_getLatestSuiSystemState":
        result = {"epoch": "1", "protocolVersion": "1", "referenceGasPrice": "100"}
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
                    "url": "http://127.0.0.1:9000/a",
                    "family": "sui",
                },
                {
                    "name": "b",
                    "url": "http://127.0.0.1:9000/b",
                    "family": "sui",
                },
            ]
        }
    )


def test_sui_adapter_and_pin_params() -> None:
    assert SUI.head_method == "sui_getLatestCheckpointSequenceNumber"
    assert SUI.ws_method == ""
    assert pin_block_params(SUI, 100) == ["100"]
    assert resolve_block_time(SUI, chain_id=None, override=None) == 0.5
    assert parse_block_height("100") == 100
    assert parse_block_height(_CHECKPOINT) == 100
    assert parse_block_hash(_CHECKPOINT) == _DIGEST
    assert parse_client_label("35834a8a") == "35834a8a"


def test_sui_workloads_use_sui_methods() -> None:
    for name in ("general", "wallet", "indexer", "trading", "nft"):
        steps = family_workload(FAMILY_SUI, name)
        methods = {spec.method for spec in steps}
        assert "sui_getLatestCheckpointSequenceNumber" in methods
        assert all(not m.startswith("eth_") for m in methods)
        assert all(not m.startswith("trace_") for m in methods)
        assert all(not m.startswith("debug_") for m in methods)
    with pytest.raises(MethodError, match="no sui mix"):
        family_workload(FAMILY_SUI, "tracing")


def test_compare_two_sui_endpoints_mock() -> None:
    plan = resolve_workload(
        profile=None,
        workload="general",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_SUI,
    )
    client = httpx.Client(transport=httpx.MockTransport(_sui_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="general",
        family=FAMILY_SUI,
        client=client,
    )
    assert result.family == "sui"
    assert result.block_time_s == 0.5
    assert len(result.outcomes) == 2
    for outcome in result.outcomes:
        assert outcome.stats.n_ok >= 1
        methods = {hit.method for hit in outcome.samples if hit.method}
        assert "sui_getLatestCheckpointSequenceNumber" in methods
        assert "eth_blockNumber" not in methods
        assert outcome.freshness is not None
        assert outcome.freshness.height == 100
        assert outcome.consistency is not None
        assert outcome.consistency.hash == _DIGEST
    text = format_run(result, color=False)
    assert "general" in text
    assert "head" in text
    assert "eth_blockNumber" not in text


def test_evm_only_extras_skip_on_sui() -> None:
    assert family_skip_reason("sui") == "family"
    plan = resolve_workload(
        profile=None,
        workload="wallet",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_SUI,
    )
    client = httpx.Client(transport=httpx.MockTransport(_sui_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="wallet",
        family=FAMILY_SUI,
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


def test_cli_sui_compare(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import cli
    from rpcbench import run as run_mod

    cfg = tmp_path / "sui.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:9000/a\n    family: sui\n"
        "  - name: b\n    url: http://127.0.0.1:9000/b\n    family: sui\n",
        encoding="utf-8",
    )
    real = run_mod.run_endpoints

    def wrapped(*args, **kwargs):
        kwargs["client"] = httpx.Client(
            transport=httpx.MockTransport(_sui_handler)
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = cli.main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--family",
            "sui",
            "--method",
            "sui_getLatestCheckpointSequenceNumber",
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
