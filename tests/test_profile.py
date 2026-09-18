from __future__ import annotations

import random
from pathlib import Path

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.methods import ZERO_ADDRESS, MethodError, is_app_workload, resolve_workload
from rpcbench.profile import (
    RECENT_WINDOW,
    bind_workload,
    known_contract,
    load_profile,
    recent_block,
    seeded_address,
)
from rpcbench.run import run_endpoints


def _cfg() -> object:
    return parse_endpoints(
        {
            "endpoints": [
                {"name": "a", "url": "http://127.0.0.1:8545/a"},
                {"name": "b", "url": "http://127.0.0.1:8545/b"},
            ]
        }
    )


def test_load_yaml_profile_without_code_changes(tmp_path: Path) -> None:
    path = tmp_path / "dex.yaml"
    path.write_text(
        "name: dex\n"
        "notes: Uniswap-style reads\n"
        "timeout: 8\n"
        "methods:\n"
        "  - method: eth_blockNumber\n"
        "    weight: 1\n"
        "  - method: eth_getBlockByNumber\n"
        "    source: recent_block\n"
        "    weight: 2\n"
        "  - method: eth_getBalance\n"
        "    source: seeded_address\n"
        "    weight: 3\n",
        encoding="utf-8",
    )
    plan = resolve_workload(
        profile=str(path), method=None, preset=None, params_json=None
    )
    assert plan.label == "dex"
    assert plan.notes == "Uniswap-style reads"
    assert plan.timeout == 8.0
    assert [spec.name for spec in plan.steps] == ["head", "block", "balance"]
    assert plan.steps[1].source == "recent_block"
    assert plan.steps[2].weight == 3


def test_same_seed_and_profile_same_params() -> None:
    from rpcbench.methods import CallSpec

    steps = (
        CallSpec("block", "eth_getBlockByNumber", (), source="recent_block"),
        CallSpec("balance", "eth_getBalance", (), source="seeded_address"),
    )
    left, fell_a = bind_workload(steps, seed=7, head=1000, chain_id=1)
    right, fell_b = bind_workload(steps, seed=7, head=1000, chain_id=1)
    assert fell_a is False and fell_b is False
    assert left == right
    assert left[0].params[0] != "latest"
    assert str(left[0].params[0]).startswith("0x")
    assert left[1].params[0] == seeded_address(7, 1)
    other, _ = bind_workload(steps, seed=8, head=1000, chain_id=1)
    assert other[0].params != left[0].params or other[1].params != left[1].params


def test_recent_block_comes_from_head_not_hardcoded_hash() -> None:
    from rpcbench.methods import CallSpec

    steps = (CallSpec("block", "eth_getBlockByNumber", (), source="recent_block"),)
    bound, fell = bind_workload(steps, seed=1, head=1000, chain_id=None)
    assert fell is False
    height = int(str(bound[0].params[0]), 16)
    assert 1000 - RECENT_WINDOW <= height <= 1000
    assert bound[0].params[0].lower() != "0xdeadbeef"
    assert recent_block(1000, random.Random(1)) == height


def test_known_contract_uses_chain_id_table() -> None:
    from rpcbench.methods import CallSpec

    steps = (CallSpec("call", "eth_call", (), source="known_contract"),)
    bound, fell = bind_workload(steps, seed=0, head=None, chain_id=1)
    assert fell is False
    assert bound[0].params[0]["to"] == known_contract(1)
    assert bound[0].params[0]["to"] != ZERO_ADDRESS


def test_fixture_fallback_when_head_missing() -> None:
    from rpcbench.methods import CallSpec

    steps = (CallSpec("block", "eth_getBlockByNumber", (), source="recent_block"),)
    bound, fell = bind_workload(steps, seed=3, head=None, chain_id=None)
    assert fell is True
    assert bound[0].params == ("latest", False)


def test_rejects_trace_filter_and_debug_in_yaml() -> None:
    from rpcbench.profile import parse_profile

    with pytest.raises(MethodError, match="not allowed"):
        parse_profile(
            {"methods": [{"method": "trace_filter", "weight": 1}]},
            source="t.yaml",
        )
    with pytest.raises(MethodError, match="not allowed"):
        parse_profile(
            {"methods": [{"method": "debug_traceBlockByNumber", "weight": 1}]},
            source="t.yaml",
        )


def test_rejects_writes_without_flag() -> None:
    from rpcbench.profile import parse_profile

    with pytest.raises(MethodError, match="write method"):
        parse_profile(
            {"methods": [{"method": "eth_sendRawTransaction"}]},
            source="t.yaml",
        )
    plan = parse_profile(
        {"name": "send", "methods": [{"method": "eth_sendRawTransaction"}]},
        source="t.yaml",
        allow_writes=True,
    )
    assert plan.steps[0].method == "eth_sendRawTransaction"


def test_missing_profile_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(MethodError, match="not found"):
        load_profile(missing)
    with pytest.raises(MethodError, match="not found"):
        resolve_workload(
            profile=str(missing), method=None, preset=None, params_json=None
        )


def test_is_app_workload_custom_name() -> None:
    assert is_app_workload("dex") is True
    assert is_app_workload("wallet") is True
    assert is_app_workload("eth_blockNumber") is False
    assert is_app_workload("single") is False


def test_run_uses_chain_head_same_sequence_across_providers() -> None:
    import json

    by_url: dict[str, list[object]] = {"/a": [], "/b": []}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "0x3e8"}
            )
        if method == "eth_getBlockByNumber":
            params = payload.get("params") or []
            path = request.url.path
            if params and params[0] not in {"latest", "safe", "finalized"}:
                by_url.setdefault(path, []).append(params[0])
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"hash": "0xabc", "number": params[0]},
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    from rpcbench.methods import CallSpec

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        _cfg(),
        method="dex",
        samples=1,
        warmup=0,
        budget=32,
        seed=7,
        client=client,
        profile="dex",
        workload=(
            CallSpec("block", "eth_getBlockByNumber", (), source="recent_block"),
        ),
    )
    assert result.payload is not None
    assert result.payload.source == "chain"
    assert result.payload.head == 1000
    assert result.payload.fallback is False
    pinned = result.workload[0].params[0]
    assert pinned != "0xdeadbeef"
    assert pinned != "latest"
    assert pinned in by_url["/a"]
    assert by_url["/a"] == by_url["/b"]
    again = run_endpoints(
        _cfg(),
        method="dex",
        samples=1,
        warmup=0,
        budget=32,
        seed=7,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        profile="dex",
        workload=(
            CallSpec("block", "eth_getBlockByNumber", (), source="recent_block"),
        ),
    )
    assert again.sequence_id == result.sequence_id
    assert again.workload[0].params == result.workload[0].params


def test_run_falls_back_to_latest_when_head_fails() -> None:
    import json

    timed_params: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_blockNumber":
            return httpx.Response(500, text="no")
        if method == "eth_getBlockByNumber":
            params = payload.get("params") or []
            if params and params[0] == "latest":
                timed_params.append(params)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"hash": "0x1"}},
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    from rpcbench.methods import CallSpec

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        parse_endpoints(
            {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
        ),
        method="dex",
        samples=1,
        warmup=0,
        budget=16,
        seed=1,
        client=client,
        profile="dex",
        workload=(
            CallSpec("block", "eth_getBlockByNumber", (), source="recent_block"),
        ),
    )
    assert result.payload is not None
    assert result.payload.source == "fixture"
    assert result.payload.fallback is True
    assert result.workload[0].params[0] == "latest"
    assert timed_params
