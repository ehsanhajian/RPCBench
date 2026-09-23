"""Solana family benchmark mix. No live public RPCs."""

from __future__ import annotations

import json

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.consistency import parse_block_hash
from rpcbench.family import SOLANA, pin_block_params, resolve_block_time
from rpcbench.logs import family_skip_reason
from rpcbench.methods import FAMILY_SOLANA, MethodError, family_workload, resolve_workload
from rpcbench.report import format_run
from rpcbench.run import run_endpoints


_GENESIS = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
_BLOCKHASH = "EkSnNWid2cvwEVmUhHhXuHQHFRHC95CGoZGioguQmQN"


def _solana_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload.get("method")
    ident = payload.get("id", 1)
    if method == "getSlot":
        result: object = 100
    elif method == "getGenesisHash":
        result = _GENESIS
    elif method == "getLatestBlockhash":
        result = {"blockhash": _BLOCKHASH, "lastValidBlockHeight": 99}
    elif method == "getBalance":
        result = {"context": {"slot": 100}, "value": 1}
    elif method == "getAccountInfo":
        result = {"context": {"slot": 100}, "value": None}
    elif method == "getSignaturesForAddress":
        result = []
    elif method == "getBlock":
        result = {
            "blockhash": _BLOCKHASH,
            "previousBlockhash": _GENESIS,
            "parentSlot": 99,
            "blockHeight": 50,
        }
    elif method == "getVersion":
        result = {"solana-core": "2.0.0", "feature-set": 1}
    elif method == "getHealth":
        result = "ok"
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
                    "url": "http://127.0.0.1:8899/a",
                    "family": "solana",
                },
                {
                    "name": "b",
                    "url": "http://127.0.0.1:8899/b",
                    "family": "solana",
                },
            ]
        }
    )


def test_solana_adapter_and_pin_params() -> None:
    assert SOLANA.head_method == "getSlot"
    assert pin_block_params(SOLANA, 42)[0] == 42
    assert resolve_block_time(SOLANA, chain_id=None, override=None) == 0.4
    assert parse_block_hash({"blockhash": _BLOCKHASH}) == _BLOCKHASH


def test_solana_workloads_use_solana_methods() -> None:
    for name in ("general", "wallet", "indexer", "trading", "nft"):
        steps = family_workload(FAMILY_SOLANA, name)
        methods = {spec.method for spec in steps}
        assert "getSlot" in methods
        assert all(not m.startswith("eth_") for m in methods)
        assert all(not m.startswith("trace_") for m in methods)
        assert all(not m.startswith("debug_") for m in methods)
    with pytest.raises(MethodError, match="no solana mix"):
        family_workload(FAMILY_SOLANA, "tracing")


def test_compare_two_solana_endpoints_mock() -> None:
    plan = resolve_workload(
        profile=None,
        workload="general",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_SOLANA,
    )
    client = httpx.Client(transport=httpx.MockTransport(_solana_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="general",
        family=FAMILY_SOLANA,
        client=client,
    )
    assert result.family == "solana"
    assert result.block_time_s == 0.4
    assert len(result.outcomes) == 2
    for outcome in result.outcomes:
        assert outcome.stats.n_ok >= 1
        methods = {hit.method for hit in outcome.samples if hit.method}
        assert "getSlot" in methods
        assert "eth_blockNumber" not in methods
    text = format_run(result, color=False)
    assert "general" in text
    assert "head" in text
    assert "eth_blockNumber" not in text
    assert all(
        outcome.consistency is not None and outcome.consistency.hash == _BLOCKHASH
        for outcome in result.outcomes
    )

def test_evm_only_extras_skip_on_solana() -> None:
    assert family_skip_reason("solana") == "family"
    plan = resolve_workload(
        profile=None,
        workload="wallet",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_SOLANA,
    )
    client = httpx.Client(transport=httpx.MockTransport(_solana_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="wallet",
        family=FAMILY_SOLANA,
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


def test_cli_solana_compare(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import cli
    from rpcbench import run as run_mod

    cfg = tmp_path / "sol.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:8899/a\n    family: solana\n"
        "  - name: b\n    url: http://127.0.0.1:8899/b\n    family: solana\n",
        encoding="utf-8",
    )
    real = run_mod.run_endpoints

    def wrapped(*args, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(_solana_handler))
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = cli.main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--family",
            "solana",
            "--method",
            "getSlot",
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
    assert "getSlot" in out or "a" in out
