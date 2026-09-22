from __future__ import annotations

import json

import httpx

from rpcbench.config import parse_endpoints
from rpcbench.coverage import cell_for, is_coverage_miss
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.markdown import format_md
from rpcbench.methods import (
    apply_simulate,
    family_workload,
    simulate_params,
)
from rpcbench.report import format_run, run_to_dict
from rpcbench.run import run_endpoints


def _cfg():
    return parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )


def test_apply_simulate_appends_call_gas_and_optional_simulate() -> None:
    from rpcbench.methods import CallSpec

    steps = (CallSpec("head", "eth_blockNumber", ()),)
    out = apply_simulate(steps)
    methods = [spec.method for spec in out]
    assert methods == [
        "eth_blockNumber",
        "eth_call",
        "eth_estimateGas",
        "eth_simulateV1",
    ]
    sim = out[-1]
    assert sim.optional is True
    assert sim.params == simulate_params()
    wallet = apply_simulate(family_workload("evm", "wallet"))
    assert [spec.method for spec in wallet].count("eth_estimateGas") == 1
    assert "eth_simulateV1" in {spec.method for spec in wallet}


def test_simulate_methods_appear_in_per_method_table() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "eth_simulateV1":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": []}
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x5208"}
        )

    steps = apply_simulate(family_workload("evm", "wallet"))
    result = run_endpoints(
        _cfg(),
        method="wallet",
        samples=1,
        warmup=0,
        budget=32,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        workload=steps,
        profile="wallet",
        simulate=True,
    )
    names = {spec.method for spec in result.workload}
    assert "eth_estimateGas" in names
    assert "eth_simulateV1" in names
    by_method = {name: stats for name, stats in result.outcomes[0].by_method}
    assert by_method["gas"].n_ok == 2
    assert by_method["simulate"].n_ok == 1
    text = format_run(result, verbose=True, color=False)
    assert "Methods  (per-method" in text
    assert "eth_estimateGas" in text
    assert "eth_simulateV1" in text
    data = run_to_dict(result)
    steps_json = {row["method"] for row in data["methods"]}
    assert "eth_estimateGas" in steps_json
    assert "eth_simulateV1" in steps_json
    assert data.get("simulate") is True
    assert "eth_send" not in json.dumps(data)
    assert "finding" not in text.lower()


def test_missing_simulateV1_is_skip_not_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "eth_simulateV1":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {
                        "code": -32601,
                        "message": "the method eth_simulateV1 does not exist/is not available",
                    },
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    steps = apply_simulate(family_workload("evm", "wallet"))
    result = run_endpoints(
        _cfg(),
        method="wallet",
        samples=1,
        warmup=0,
        budget=32,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        workload=steps,
        profile="wallet",
        simulate=True,
    )
    outcome = result.outcomes[0]
    sim = next(spec for spec in result.workload if spec.name == "simulate")
    cell = cell_for(outcome, sim, result)
    assert cell.status == "skip"
    assert not is_coverage_miss(outcome, result)
    assert outcome.stats.n_fail == 0
    text = format_run(result, color=False)
    assert "simulate" in text
    assert "Methods  (per-method" in text
    data = run_to_dict(result)
    assert data["coverage"]["providers"][0]["cells"]["simulate"]["status"] == "skip"
    assert data["coverage"]["providers"][0]["missed"] == []
    assert data["summary"]["coverage_miss_names"] == []
    assert data["ranking"][0]["rank"] == 1
    assert data["ranking"][0]["reliable"] is True
    assert data.get("simulate") is True
    html = format_html(result)
    md = format_md(result)
    csv_text = format_csv(result)
    assert "eth_simulateV1" in html or "simulate" in html
    assert "## Simulation" in md
    assert "skip" in md
    assert "skip" in csv_text
    assert "finding" not in html.lower()
    assert "finding" not in md.lower()


def test_default_catalogs_have_no_writes_or_simulateV1() -> None:
    from rpcbench.methods import WORKLOADS, _WRITE_PREFIXES

    for steps in WORKLOADS["evm"].values():
        for spec in steps:
            lower = spec.method.lower()
            assert not any(lower.startswith(p) for p in _WRITE_PREFIXES)
            assert spec.method != "eth_simulateV1"
            assert spec.method != "eth_sendRawTransaction"


def test_cli_simulate(tmp_path, monkeypatch, capsys) -> None:
    from rpcbench import run as run_mod
    from rpcbench.cli import main

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = json.loads(request.content)["method"]
        methods.append(method)
        if method == "eth_simulateV1":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--simulate",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "simulate" in out.lower()
    assert "eth_estimateGas" in methods
    assert "eth_simulateV1" in methods
    assert "eth_call" in methods
    assert not any(m.startswith("eth_send") for m in methods)
    assert "Method    general" in out


def test_yaml_simulateV1_is_always_optional(tmp_path) -> None:
    from rpcbench.methods import resolve_workload

    path = tmp_path / "sim.yaml"
    path.write_text(
        "name: sim\n"
        "methods:\n"
        "  - method: eth_blockNumber\n"
        "  - method: eth_estimateGas\n"
        "  - method: eth_simulateV1\n"
        "    optional: false\n",
        encoding="utf-8",
    )
    plan = resolve_workload(
        profile=str(path), method=None, preset=None, params_json=None
    )
    by_name = {spec.name: spec for spec in plan.steps}
    assert by_name["gas"].method == "eth_estimateGas"
    assert by_name["gas"].optional is False
    assert by_name["simulate"].optional is True
    assert by_name["simulate"].params == simulate_params()
