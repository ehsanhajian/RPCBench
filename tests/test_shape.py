from __future__ import annotations

import json

import httpx
import pytest

from rpcbench.cli import main
from rpcbench.config import Endpoint
from rpcbench.html import format_html
from rpcbench.methods import CallSpec
from rpcbench.prometheus import format_prometheus, validate_prometheus_text
from rpcbench.report import format_run, run_to_dict
from rpcbench.rpc import ProbeResult, RequestBudget
from rpcbench.run import EndpointOutcome, RunResult, summarize
from rpcbench.shape import (
    SHAPE_SPECS,
    measure_shape,
    normalize_shape,
    shape_dict,
    target_rps_at,
)


def test_normalize_shape() -> None:
    assert normalize_shape(None) is None
    assert normalize_shape("off") is None
    assert normalize_shape("RAMP") == "ramp"
    with pytest.raises(ValueError, match="shape must"):
        normalize_shape("chaos")


def test_target_rps_curves() -> None:
    assert target_rps_at(SHAPE_SPECS["flat"], 0) == 4.0
    assert target_rps_at(SHAPE_SPECS["ramp"], 0) == 1.0
    assert target_rps_at(SHAPE_SPECS["ramp"], 12) == 6.0
    assert target_rps_at(SHAPE_SPECS["spike"], 5) == 8.0
    assert target_rps_at(SHAPE_SPECS["spike"], 1) == 2.0
    assert target_rps_at(SHAPE_SPECS["soak"], 10) == 2.0


def test_measure_shape_exports_series(monkeypatch) -> None:
    from rpcbench.shape import ShapeSpec

    monkeypatch.setitem(
        SHAPE_SPECS,
        "flat",
        ShapeSpec(
            name="flat",
            duration_s=0.25,
            max_requests=5,
            max_concurrency=2,
            start_rps=40.0,
            end_rps=40.0,
        ),
    )
    calls = {"n": 0}

    def hit(endpoint, *, spec, timeout, budget, deadline):
        del endpoint, spec, timeout, budget, deadline
        calls["n"] += 1
        return ProbeResult(
            ok=True,
            reachable=True,
            latency_ms=10.0 + calls["n"],
            result="0x1",
            error=None,
            error_class=None,
            attempts=1,
        )

    summary = measure_shape(
        Endpoint(name="local", url="http://127.0.0.1/"),
        spec=CallSpec("head", "eth_blockNumber", ()),
        shape="flat",
        timeout=1.0,
        budget=RequestBudget(64),
        deadline=None,
        hit=hit,
    )
    assert summary.shape == "flat"
    assert summary.n >= 1
    assert summary.series
    assert summary.series[0].t_s == 0.0
    blob = shape_dict(summary)
    assert blob["series"]
    assert "rps" in blob["series"][0]


def test_cli_shape_ramp(tmp_path, monkeypatch, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ident = payload.get("id", 1)
        method = payload.get("method")
        if method == "eth_chainId":
            result: object = "0x1"
        elif method == "eth_blockNumber":
            result = "0x10"
        elif method == "eth_getBlockByNumber":
            result = {
                "number": "0x10",
                "hash": "0x" + "11" * 32,
                "parentHash": "0x" + "22" * 32,
            }
        else:
            result = "0x0"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": ident, "result": result}
        )

    transport = httpx.MockTransport(handler)

    def fake_client(**kwargs):
        return httpx.Client(transport=transport, timeout=5.0)

    monkeypatch.setattr("rpcbench.run.make_client", fake_client)
    from rpcbench.shape import ShapeSpec

    monkeypatch.setitem(
        SHAPE_SPECS,
        "ramp",
        ShapeSpec(
            name="ramp",
            duration_s=0.3,
            max_requests=6,
            max_concurrency=2,
            start_rps=20.0,
            end_rps=40.0,
        ),
    )
    code = main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_blockNumber",
            "--samples",
            "1",
            "--warmup",
            "0",
            "--shape",
            "ramp",
            "--json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["shape"] == "ramp"
    shapes = [row.get("shape") for row in data["ranking"] if row.get("shape")]
    assert shapes
    assert shapes[0]["series"]
    assert shapes[0]["n"] >= 1


def test_report_and_prometheus_include_shape_series() -> None:
    from rpcbench.shape import ShapeSummary, ShapeWindow

    series = (
        ShapeWindow(0.0, 4.0, 3.5, 12.0, 0.0, 7, 0),
        ShapeWindow(2.0, 4.0, 4.0, 15.0, 0.1, 8, 1),
    )
    summary = ShapeSummary(
        shape="flat",
        method="eth_blockNumber",
        duration_s=8.0,
        max_requests=32,
        max_concurrency=2,
        n_ok=15,
        n_fail=1,
        n=16,
        rate_limit=0,
        series=series,
        error=None,
        error_class=None,
    )
    outcome = EndpointOutcome(
        endpoint=Endpoint(name="local", url="http://127.0.0.1/x"),
        warmup=(),
        samples=(
            ProbeResult(
                ok=True,
                reachable=True,
                latency_ms=10.0,
                result="0x1",
                error=None,
                error_class=None,
                attempts=1,
            ),
        ),
        stats=summarize(
            (
                ProbeResult(
                    ok=True,
                    reachable=True,
                    latency_ms=10.0,
                    result="0x1",
                    error=None,
                    error_class=None,
                    attempts=1,
                ),
            )
        ),
        shape=summary,
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=1,
        warmup=0,
        timeout=5.0,
        budget=32,
        outcomes=(outcome,),
        budget_remaining=16,
        shape="flat",
        family="evm",
    )
    text = format_run(result)
    assert "Shape" in text
    assert "flat" in text
    html = format_html(result)
    assert "Load shape" in html
    assert "shape-chart" in html
    prom = format_prometheus(result)
    assert "rpcbench_shape_rps" in prom
    assert 'offset_s="0"' in prom
    assert validate_prometheus_text(prom) == []
    data = run_to_dict(result)
    assert data["shape"] == "flat"
    assert data["ranking"][0]["shape"]["series"]


def test_shape_defaults_are_conservative() -> None:
    for name, spec in SHAPE_SPECS.items():
        assert spec.duration_s <= 20.0, name
        assert spec.max_requests <= 48, name
        assert spec.max_concurrency <= 4, name
