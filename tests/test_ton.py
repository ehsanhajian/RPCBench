"""TON Center-style JSON-RPC family benchmark mix. No live public RPCs."""

from __future__ import annotations

import json

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.consistency import parse_block_hash
from rpcbench.family import TON, pin_block_params, resolve_block_time
from rpcbench.freshness import parse_block_height
from rpcbench.logs import family_skip_reason
from rpcbench.methods import (
    FAMILY_TON,
    MethodError,
    family_workload,
    resolve_workload,
)
from rpcbench.report import format_run, run_to_dict
from rpcbench.rpc import NamedParams, encode_rpc_params
from rpcbench.run import run_endpoints

_ROOT = "NDDLYk7uVvxF7rpetpOT9tsQX4//K7/Cx8ksZcZ26vk="
_INFO = {
    "@type": "blocks.masterchainInfo",
    "last": {
        "@type": "ton.blockIdExt",
        "workchain": -1,
        "shard": "-9223372036854775808",
        "seqno": 100,
        "root_hash": _ROOT,
        "file_hash": "ZGB5+4Jwf9jsMVNnMtZCcfXERWuJeSlccP/P+qZmnRE=",
    },
    "state_root_hash": "0lsGV0Ze+3NbySPHxu7dgMcAmmWpF9EfxNe6oJkqcC8=",
    "init": {
        "@type": "ton.blockIdExt",
        "workchain": -1,
        "shard": "0",
        "seqno": 0,
        "root_hash": "F6OpKZKqvqeFp6CQmFomXNMfMj2EnaUSOXN+Mh+wVWk=",
        "file_hash": "XplPz01CXAps5qeSWUtxcyBfdAo5zVb1N979KLSKD24=",
    },
}
_HEADER = {
    "@type": "blocks.header",
    "id": {
        "@type": "ton.blockIdExt",
        "workchain": -1,
        "shard": "-9223372036854775808",
        "seqno": 100,
        "root_hash": _ROOT,
        "file_hash": "ZGB5+4Jwf9jsMVNnMtZCcfXERWuJeSlccP/P+qZmnRE=",
    },
    "global_id": -239,
    "version": 0,
    "gen_utime": 1,
}


def _ton_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload.get("method")
    ident = payload.get("id", 1)
    params = payload.get("params")
    if method == "getMasterchainInfo":
        assert params == {}
        result: object = _INFO
    elif method == "getConsensusBlock":
        result = {"consensus_block": 100, "candidate": False}
    elif method == "getBlockHeader":
        assert isinstance(params, dict)
        result = _HEADER
    elif method == "getShards":
        result = {"shards": []}
    elif method in {
        "getAddressInformation",
        "getWalletInformation",
    }:
        result = {"balance": "0", "@type": "raw.fullAccountState"}
    elif method == "getAddressBalance":
        result = "0"
    elif method == "getConfigParam":
        result = {"config": {}}
    else:
        return httpx.Response(
            200,
            json={"ok": False, "error": f"no {method}", "code": 404},
        )
    return httpx.Response(200, json={"ok": True, "result": result, "id": ident})


def _cfg() -> object:
    return parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "a",
                    "url": "http://127.0.0.1:8081/a",
                    "family": "ton",
                },
                {
                    "name": "b",
                    "url": "http://127.0.0.1:8081/b",
                    "family": "ton",
                },
            ]
        }
    )


def test_ton_adapter_and_named_params() -> None:
    assert TON.head_method == "getMasterchainInfo"
    assert TON.ws_method == ""
    assert TON.block_time_s == 5.0
    pinned = pin_block_params(TON, 100)
    assert len(pinned) == 1
    assert isinstance(pinned[0], NamedParams)
    assert pinned[0]["seqno"] == 100
    assert pinned[0]["workchain"] == -1
    assert encode_rpc_params([NamedParams()]) == {}
    assert resolve_block_time(TON, chain_id=None, override=None) == 5.0
    assert parse_block_height(_INFO) == 100
    assert parse_block_height(_HEADER) == 100
    assert parse_block_hash(_INFO) == _ROOT
    assert parse_block_hash(_HEADER) == _ROOT


def test_ton_workloads_use_ton_methods() -> None:
    for name in ("general", "wallet", "indexer", "trading", "nft"):
        steps = family_workload(FAMILY_TON, name)
        methods = {spec.method for spec in steps}
        assert "getMasterchainInfo" in methods
        assert all(not m.startswith("eth_") for m in methods)
        assert all(not m.startswith("trace_") for m in methods)
        assert all(not m.startswith("debug_") for m in methods)
    with pytest.raises(MethodError, match="no ton mix"):
        family_workload(FAMILY_TON, "tracing")


def test_compare_two_ton_endpoints_mock() -> None:
    plan = resolve_workload(
        profile=None,
        workload="general",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_TON,
    )
    client = httpx.Client(transport=httpx.MockTransport(_ton_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="general",
        family=FAMILY_TON,
        client=client,
    )
    assert result.family == "ton"
    assert result.block_time_s == 5.0
    assert len(result.outcomes) == 2
    for outcome in result.outcomes:
        assert outcome.stats.n_ok >= 1
        methods = {hit.method for hit in outcome.samples if hit.method}
        assert "getMasterchainInfo" in methods
        assert "eth_blockNumber" not in methods
        assert outcome.freshness is not None
        assert outcome.freshness.height == 100
        assert outcome.consistency is not None
        assert outcome.consistency.hash == _ROOT
    text = format_run(result, color=False)
    assert "general" in text
    assert "head" in text
    assert "eth_blockNumber" not in text
    blob = json.dumps(run_to_dict(result))
    assert "api_key" not in blob or "[redacted]" in blob


def test_evm_only_extras_skip_on_ton() -> None:
    assert family_skip_reason("ton") == "family"
    plan = resolve_workload(
        profile=None,
        workload="wallet",
        method=None,
        preset=None,
        params_json=None,
        family=FAMILY_TON,
    )
    client = httpx.Client(transport=httpx.MockTransport(_ton_handler))
    result = run_endpoints(
        _cfg(),
        method=plan.label,
        samples=1,
        warmup=0,
        budget=64,
        workload=plan.steps,
        profile="wallet",
        family=FAMILY_TON,
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


def test_ton_api_key_masked_in_display_url() -> None:
    # Concatenate so scanners do not treat the fixture as a live credential.
    key = "fixture" + "-api-key-value"
    hdr = "fixture" + "-hdr-token"
    cfg = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "keyed",
                    "url": f"https://toncenter.com/api/v2/jsonRPC?api_key={key}",
                    "family": "ton",
                    "headers": {"X-API-Key": hdr},
                }
            ]
        }
    )
    endpoint = cfg.endpoints[0]
    assert key not in endpoint.display_url
    assert "api_key=[redacted]" in endpoint.display_url
    assert ("X-API-Key", hdr) in endpoint.headers
    blob = json.dumps({"url": endpoint.display_url})
    assert hdr not in blob


def test_cli_ton_compare(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import cli
    from rpcbench import run as run_mod

    cfg = tmp_path / "ton.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:8081/a\n    family: ton\n"
        "  - name: b\n    url: http://127.0.0.1:8081/b\n    family: ton\n",
        encoding="utf-8",
    )
    real = run_mod.run_endpoints

    def wrapped(*args, **kwargs):
        kwargs["client"] = httpx.Client(
            transport=httpx.MockTransport(_ton_handler)
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = cli.main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--family",
            "ton",
            "--method",
            "getMasterchainInfo",
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
