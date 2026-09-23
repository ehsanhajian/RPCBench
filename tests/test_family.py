"""Family adapter: one EVM benchmark, no scan findings."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from rpcbench.config import ConfigError, parse_endpoints
from rpcbench.family import (
    EVM,
    benchmark_family,
    detect_family,
    normalize_family,
    resolve_benchmark_family,
)
from rpcbench.rpc import ProbeResult

ROOT = Path(__file__).resolve().parents[1]
CORE_METRICS = (
    "reliability.py",
    "verdict.py",
    "recommend.py",
    "coverage.py",
)


def test_core_metrics_do_not_name_evm_methods() -> None:
    for name in CORE_METRICS:
        text = (ROOT / "src" / "rpcbench" / name).read_text(encoding="utf-8")
        tree = ast.parse(text)
        imported = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        blob = text + "\n" + "\n".join(imported)
        assert "eth_" not in blob, name


def test_any_evm_chain_uses_one_adapter() -> None:
    listed = benchmark_family("evm")
    unknown = benchmark_family("EVM")
    assert listed is unknown is EVM
    assert listed.batch_shape == "jsonrpc-array"
    assert listed.ws_method == "eth_subscribe"
    assert listed.head_method == "eth_blockNumber"
    for chain_id in (1, 137, 999_999):
        assert _chain_is_evm(chain_id)


def _chain_is_evm(chain_id: int) -> bool:
    hit = ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=1.0,
        result=hex(chain_id),
        error=None,
        error_class=None,
        attempts=1,
    )
    from rpcbench.family import _identity_hit

    return _identity_hit("eth_chainId", hit)


def test_unimplemented_family_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="no benchmark mix"):
        parse_endpoints(
            {
                "endpoints": [
                    {
                        "name": "dot",
                        "url": "http://127.0.0.1:9933",
                        "family": "substrate",
                    }
                ]
            }
        )
    with pytest.raises(ConfigError, match="unknown family"):
        normalize_family("nope")


def test_solana_family_loads() -> None:
    cfg = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "sol",
                    "url": "http://127.0.0.1:8899",
                    "family": "solana",
                }
            ]
        }
    )
    assert cfg.endpoints[0].family == "solana"
    assert resolve_benchmark_family(cfg) == "solana"
    adapter = benchmark_family("solana")
    assert adapter.head_method == "getSlot"
    assert adapter.ws_method == "slotSubscribe"
    assert adapter.block_time_s == 0.4


def test_omitted_family_is_evm_and_localhost_stays_allowed() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:8545"}]}
    )
    assert cfg.endpoints[0].family == "evm"
    assert resolve_benchmark_family(cfg) == "evm"


def test_auto_detect_unknown_chain_id_is_evm() -> None:
    cfg = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "local",
                    "url": "http://127.0.0.1:8545",
                    "family": "auto",
                }
            ]
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        assert "eth_chainId" in body
        assert "admin_" not in body
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": "0x186a0"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert detect_family(cfg.endpoints[0], timeout=1.0, client=client) == "evm"
    assert (
        resolve_benchmark_family(cfg, override="auto", timeout=1.0, client=client)
        == "evm"
    )


def test_auto_detect_solana_resolves() -> None:
    cfg = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "local",
                    "url": "http://127.0.0.1:8899",
                    "family": "auto",
                }
            ]
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "eth_chainId" in body:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
        if "getHealth" in body:
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "ok"}
            )
        raise AssertionError(body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    found = detect_family(cfg.endpoints[0], timeout=1.0, client=client)
    assert found == "solana"
    assert resolve_benchmark_family(cfg, timeout=1.0, client=client) == "solana"


def test_auto_detect_unimplemented_family_is_not_a_finding() -> None:
    cfg = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "local",
                    "url": "http://127.0.0.1:9933",
                    "family": "auto",
                }
            ]
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "eth_chainId" in body:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
        if "getHealth" in body:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
        if "system_health" in body:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"peers": 1, "isSyncing": False, "shouldHavePeers": True},
                },
            )
        raise AssertionError(body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    found = detect_family(cfg.endpoints[0], timeout=1.0, client=client)
    assert found == "substrate"
    with pytest.raises(ConfigError, match="no benchmark mix") as exc:
        resolve_benchmark_family(cfg, timeout=1.0, client=client)
    blob = str(exc.value).lower()
    for word in ("finding", "severity", "cve", "disclosure", "vulnerability"):
        assert word not in blob


def test_detect_failure_is_a_config_error_not_a_finding() -> None:
    cfg = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "local",
                    "url": "http://127.0.0.1:8545",
                    "family": "auto",
                }
            ]
        }
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": None})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ConfigError, match="could not detect") as exc:
        detect_family(cfg.endpoints[0], timeout=1.0, client=client)
    blob = str(exc.value).lower()
    for word in ("finding", "severity", "cve", "disclosure", "vulnerability"):
        assert word not in blob


def test_mixed_evm_and_solana_errors() -> None:
    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "a", "url": "http://127.0.0.1:8545", "family": "evm"},
                {"name": "b", "url": "http://127.0.0.1:8899", "family": "auto"},
            ]
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "eth_chainId" in body:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
        if "getHealth" in body:
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "ok"}
            )
        raise AssertionError(body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ConfigError, match="more than one family") as exc:
        resolve_benchmark_family(cfg, timeout=1.0, client=client)
    assert "finding" not in str(exc.value).lower()


def test_cli_family_override_is_a_config_error(tmp_path, capsys) -> None:
    from rpcbench.cli import main

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(
        ["run", "--endpoints", str(cfg), "--family", "substrate", "--samples", "1"]
    )
    assert code == 2
    err = capsys.readouterr().err.lower()
    assert "no benchmark mix" in err
    assert "finding" not in err
