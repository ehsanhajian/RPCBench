"""Aptos fullnode REST family benchmark mix. No live public RPCs."""

from __future__ import annotations

import json
from urllib.parse import unquote

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.consistency import parse_block_hash
from rpcbench.family import APTOS, pin_block_params, resolve_block_time
from rpcbench.freshness import parse_block_height
from rpcbench.logs import family_skip_reason
from rpcbench.methods import (
    FAMILY_APTOS,
    MethodError,
    family_workload,
    resolve_workload,
)
from rpcbench.report import format_run
from rpcbench.rpc import rest_url
from rpcbench.run import run_endpoints
from rpcbench.tags import parse_client_label

_HASH = "0x" + "ab" * 32
_LEDGER = {
    "chain_id": 1,
    "epoch": "1",
    "ledger_version": "100",
    "oldest_ledger_version": "1",
    "ledger_timestamp": "1000000",
    "node_role": "full_node",
    "oldest_block_height": "1",
    "block_height": "50",
    "git_hash": "abcdef0123456789deadbeef",
}
_BLOCK = {
    "block_height": "50",
    "block_hash": "AB" * 32,
    "block_timestamp": "1000000",
    "first_version": "90",
    "last_version": "100",
    "transactions": None,
}
_ACCOUNT = {
    "sequence_number": "0",
    "authentication_key": "0x" + "00" * 32,
}


def _aptos_handler(request: httpx.Request) -> httpx.Response:
    path = unquote(request.url.path)
    # Mock base is http://127.0.0.1:8080/v1/...
    if path.endswith("/v1") or path.endswith("/v1/"):
        result: object = _LEDGER
    elif path.rstrip("/").endswith("/accounts/0x1") and "/events/" not in path:
        result = _ACCOUNT
    elif "/resources" in path:
        result = [{"type": "0x1::account::Account", "data": {}}]
    elif "/events/" in path:
        result = [{"version": "100", "type": "0x1::block::NewBlockEvent", "data": {}}]
    elif path.rstrip("/").endswith("/transactions") or "transactions" in path:
        result = [{"version": "100", "hash": _HASH}]
    elif path.endswith("/estimate_gas_price"):
        result = {"gas_estimate": 100}
    elif "/blocks/by_version/" in path:
        result = _BLOCK
    else:
        return httpx.Response(
            404,
            json={"error_code": "not_found", "message": f"no {path}"},
        )
    return httpx.Response(200, json=result)


def _cfg() -> object:
    return parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "a",
                    "url": "http://127.0.0.1:8080/v1",
                    "family": "aptos",
                },
                {
                    "name": "b",
                    "url": "http://127.0.0.1:8080/v1",
                    "family": "aptos",
                },
            ]
        }
    )


def test_aptos_adapter_and_pin_params() -> None:
    assert APTOS.head_method == "."
    assert APTOS.transport == "rest"
    assert pin_block_params(APTOS, 100) == ["100"]
    assert resolve_block_time(APTOS, chain_id=None, override=None) == 0.1
    assert parse_block_height(_LEDGER) == 100
    assert parse_block_height(_BLOCK) == 50
    assert parse_block_hash(_BLOCK) == _HASH
    assert parse_client_label(_LEDGER) == "aptos/full_node abcdef012345"
    assert rest_url("http://127.0.0.1:8080/v1", ".", []) == "http://127.0.0.1:8080/v1"
    assert (
        rest_url("http://127.0.0.1:8080/v1", "blocks/by_version", ["100"])
        == "http://127.0.0.1:8080/v1/blocks/by_version/100"
    )


def test_aptos_workloads_use_rest_paths() -> None:
    for name in ("general", "wallet", "indexer", "trading", "nft"):
        steps = family_workload(FAMILY_APTOS, name)
        methods = {spec.method for spec in steps}
        assert "." in methods
        assert all(not m.startswith("eth_") for m in methods)
        assert all(not m.startswith("trace_") for m in methods)
        assert all(not m.startswith("debug_") for m in methods)
    with pytest.raises(MethodError, match="no aptos mix"):
        family_workload(FAMILY_APTOS, "tracing")


def test_compare_two_aptos_endpoints_mock() -> None:
    plan = resolve_workload(
        profile=None,
        workload="general",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_APTOS,
    )
    client = httpx.Client(transport=httpx.MockTransport(_aptos_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="general",
        family=FAMILY_APTOS,
        client=client,
    )
    assert result.family == "aptos"
    assert result.block_time_s == 0.1
    assert len(result.outcomes) == 2
    for outcome in result.outcomes:
        assert outcome.stats.n_ok >= 1
        methods = {hit.method for hit in outcome.samples if hit.method}
        assert "." in methods
        assert "eth_blockNumber" not in methods
        assert outcome.freshness is not None
        assert outcome.freshness.height == 100
        assert outcome.consistency is not None
        assert outcome.consistency.hash == _HASH
        assert outcome.client is not None
        assert outcome.client.startswith("aptos/")
    text = format_run(result, color=False)
    assert "general" in text
    assert "head" in text
    assert "eth_blockNumber" not in text


def test_evm_only_extras_skip_on_aptos() -> None:
    assert family_skip_reason("aptos") == "family"
    plan = resolve_workload(
        profile=None,
        workload="wallet",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_APTOS,
    )
    client = httpx.Client(transport=httpx.MockTransport(_aptos_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="wallet",
        family=FAMILY_APTOS,
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


def test_cli_aptos_compare(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import cli
    from rpcbench import run as run_mod

    cfg = tmp_path / "apt.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:8080/v1\n    family: aptos\n"
        "  - name: b\n    url: http://127.0.0.1:8080/v1\n    family: aptos\n",
        encoding="utf-8",
    )
    real = run_mod.run_endpoints

    def wrapped(*args, **kwargs):
        kwargs["client"] = httpx.Client(
            transport=httpx.MockTransport(_aptos_handler)
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = cli.main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--family",
            "aptos",
            "--method",
            ".",
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
