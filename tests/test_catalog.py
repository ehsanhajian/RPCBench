from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from rpcbench.catalog import (
    CatalogError,
    catalog_banner,
    catalog_meta,
    endpoint_from_url,
    list_chains,
    load_catalog,
    merge_targets,
    normalize_chain,
)
from rpcbench.cli import apply_job, build_parser, main
from rpcbench.config import ConfigError
from rpcbench.report import format_run
from rpcbench.run import run_endpoints


def test_list_chains_includes_ethereum() -> None:
    assert "ethereum" in list_chains()


def test_normalize_chain_aliases() -> None:
    assert normalize_chain("Ethereum") == "ethereum"
    assert normalize_chain("eth") == "ethereum"
    assert normalize_chain("mainnet") == "ethereum"


def test_unknown_chain_lists_available() -> None:
    with pytest.raises(CatalogError, match="bundled catalogs"):
        load_catalog("no-such-chain")


def test_load_ethereum_catalog() -> None:
    cfg = load_catalog("ethereum")
    assert len(cfg.endpoints) >= 2
    names = {ep.name for ep in cfg.endpoints}
    assert "publicnode" in names
    assert "drpc" in names
    for ep in cfg.endpoints:
        assert ep.url.startswith("https://")
        assert ep.family == "evm"
    meta = catalog_meta("eth")
    assert meta["version"] >= 1
    assert meta["chain_id"] == 1
    assert "rate" in catalog_banner("ethereum", cfg).lower()


def test_endpoint_from_url() -> None:
    ep = endpoint_from_url("https://example.invalid/rpc")
    assert ep.name == "example.invalid"
    assert ep.url == "https://example.invalid/rpc"
    with pytest.raises(ConfigError, match="http or https"):
        endpoint_from_url("ws://example.invalid")


def test_merge_catalog_file_and_extras(tmp_path: Path) -> None:
    path = tmp_path / "extra.yaml"
    path.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    cfg = merge_targets(
        chain="ethereum",
        endpoints_file=path,
        extra_urls=("https://extra.example/rpc",),
    )
    names = [ep.name for ep in cfg.endpoints]
    assert names[0] == "publicnode"
    assert "local" in names
    assert "extra.example" in names


def test_merge_duplicate_names_get_suffix() -> None:
    cfg = merge_targets(
        chain="ethereum",
        extra_urls=("https://publicnode.example/",),
    )
    # host-derived name may not collide; force collision via file-less extras only
    from rpcbench.catalog import _unique_names
    from rpcbench.config import Endpoint

    rows = _unique_names(
        [
            Endpoint(name="a", url="http://127.0.0.1/1"),
            Endpoint(name="a", url="http://127.0.0.1/2"),
        ]
    )
    assert [r.name for r in rows] == ["a", "a-2"]


def test_merge_requires_a_source() -> None:
    with pytest.raises(ConfigError, match="--chain"):
        merge_targets()


def test_cli_chain_flag_no_endpoints_required() -> None:
    ns = build_parser().parse_args(["compare", "--chain", "ethereum"])
    assert ns.chain == "ethereum"
    assert ns.endpoints is None
    assert ns.endpoint is None


def test_cli_endpoint_repeatable() -> None:
    ns = build_parser().parse_args(
        [
            "compare",
            "--chain",
            "eth",
            "--endpoint",
            "http://127.0.0.1:1",
            "--endpoint",
            "http://127.0.0.1:2",
        ]
    )
    assert ns.endpoint == ["http://127.0.0.1:1", "http://127.0.0.1:2"]


def test_cli_rejects_missing_targets(capsys) -> None:
    code = main(["compare", "--samples", "1"])
    assert code == 2
    assert "--chain" in capsys.readouterr().err


def test_apply_job_catalog_defaults_to_short() -> None:
    ns = build_parser().parse_args(
        ["compare", "--chain", "ethereum", "--method", "eth_blockNumber"]
    )
    apply_job(ns)
    assert ns.sample_budget == "short"


def test_catalog_compare_ranked_table_with_mock() -> None:
    """AT: catalog compare produces a ranked table (no live public RPCs)."""
    cfg = load_catalog("ethereum")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        ident = payload.get("id", 1)
        if method == "eth_chainId":
            result: object = "0x1"
        elif method == "eth_blockNumber":
            result = "0x100"
        elif method == "eth_getBlockByNumber":
            result = {
                "number": "0x100",
                "hash": "0x" + "ab" * 32,
                "parentHash": "0x" + "cd" * 32,
            }
        else:
            result = "0x0"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": ident, "result": result}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg,
        method="eth_blockNumber",
        samples=1,
        warmup=0,
        timeout=2.0,
        budget=200,
        client=client,
        family="evm",
    )
    assert len(result.outcomes) == len(cfg.endpoints)
    assert all(o.stats.n_ok >= 1 for o in result.outcomes)
    text = format_run(result, color=False)
    assert "Ranking" in text
    assert "publicnode" in text
    assert "drpc" in text


def test_cli_unknown_chain(capsys) -> None:
    code = main(["compare", "--chain", "nope", "--samples", "1"])
    assert code == 2
    err = capsys.readouterr().err
    assert "unknown chain" in err or "bundled" in err


def test_help_mentions_chain(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["compare", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--chain" in out
    assert "--endpoint" in out
    assert "ethereum" in out
