from __future__ import annotations

import csv
import io
import json

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.coverage import cell_for, is_coverage_miss
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.markdown import format_md
from rpcbench.methods import (
    CORE_WORKLOADS,
    MethodError,
    debug_trace_call_params,
    family_workload,
    is_debug_recon,
    is_debug_trace_method,
)
from rpcbench.profile import parse_profile
from rpcbench.report import format_run, run_to_dict
from rpcbench.run import run_endpoints


def _cfg():
    return parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )


def _ok_block(height: int = 1000) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"number": hex(height), "hash": "0xabc"},
        },
    )


def test_core_catalogs_do_not_call_debug() -> None:
    for name in CORE_WORKLOADS:
        steps = family_workload("evm", name)
        assert not any(spec.method.lower().startswith("debug_") for spec in steps)
    tracing = {spec.name: spec for spec in family_workload("evm", "tracing")}
    assert tracing["debug"].method == "debug_traceCall"
    assert tracing["debug"].optional is True
    assert tracing["debug"].params == debug_trace_call_params()
    assert tracing["debug"].weight == 2
    assert is_debug_trace_method("debug_traceCall")
    assert is_debug_recon("debug_memStats")
    assert is_debug_recon("debug_verbosity")
    assert not is_debug_recon("debug_traceCall")


def test_debug_trace_call_reports_latency() -> None:
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "debug_traceCall":
            seen.append(payload.get("params"))
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"type": "CALL"}}
            )
        if method == "trace_block":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)}
            )
        if method == "eth_getBlockByNumber":
            return _ok_block(1000)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    steps = family_workload("evm", "tracing")
    result = run_endpoints(
        _cfg(),
        method="tracing",
        samples=1,
        warmup=0,
        budget=64,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        workload=steps,
        profile="tracing",
    )
    assert seen
    params = seen[0]
    assert params[1] == "latest"
    assert params[2]["tracer"] == "callTracer"
    assert params[2]["timeout"] == "1s"
    by_method = {name: stats for name, stats in result.outcomes[0].by_method}
    assert by_method["debug"].n_ok == 2
    assert by_method["debug"].p50_ms is not None
    assert by_method["debug"].p95_ms is not None
    text = format_run(result, color=False)
    assert "debug_traceCall" in text
    data = run_to_dict(result)
    cell = data["coverage"]["providers"][0]["cells"]["debug"]
    assert cell["status"] == "ok"
    html = format_html(result)
    md = format_md(result)
    csv_row = next(csv.DictReader(io.StringIO(format_csv(result))))
    assert "debug_traceCall" in html
    assert "## Debug" in md
    assert csv_row["debug_status"] == "ok"
    blob = (text + html + md).lower()
    assert "finding" not in blob
    assert "vulnerab" not in blob
    assert "severity" not in blob
    assert "debug_memstats" not in blob
    assert "debug_verbosity" not in blob


def test_missing_debug_is_skip_not_finding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method in {"debug_traceCall", "trace_block"}:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)}
            )
        if method == "eth_getBlockByNumber":
            return _ok_block(1000)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    steps = family_workload("evm", "tracing")
    result = run_endpoints(
        _cfg(),
        method="tracing",
        samples=1,
        warmup=0,
        budget=64,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        workload=steps,
        profile="tracing",
    )
    outcome = result.outcomes[0]
    spec = next(item for item in result.workload if item.name == "debug")
    cell = cell_for(outcome, spec, result)
    assert cell.status == "skip"
    assert cell.skip_reason == "unsupported"
    assert not is_coverage_miss(outcome, result)
    assert outcome.stats.n_fail == 0
    data = run_to_dict(result)
    row = data["coverage"]["providers"][0]["cells"]["debug"]
    assert row["skip_reason"] == "unsupported"
    assert data["ranking"][0]["rank"] == 1
    text = format_run(result, color=False)
    html = format_html(result)
    md = format_md(result)
    csv_row = next(csv.DictReader(io.StringIO(format_csv(result))))
    assert "skip/unsupported" in text
    assert csv_row["debug_status"] == "skip/unsupported"
    blob = (text + html + md).lower()
    assert "finding" not in blob
    assert "vulnerab" not in blob
    assert "severity" not in blob


def test_yaml_debug_trace_call_optional_rejects_recon() -> None:
    plan = parse_profile(
        {
            "name": "dbg",
            "methods": [
                {"method": "eth_blockNumber"},
                {"method": "debug_traceCall", "optional": False},
            ],
        },
        source="t.yaml",
    )
    by_name = {spec.name: spec for spec in plan.steps}
    assert by_name["debug"].optional is True
    assert by_name["debug"].params == debug_trace_call_params()
    with pytest.raises(MethodError, match="not allowed"):
        parse_profile(
            {"methods": [{"method": "debug_memStats"}]},
            source="t.yaml",
        )
    with pytest.raises(MethodError, match="not allowed"):
        parse_profile(
            {"methods": [{"method": "debug_verbosity"}]},
            source="t.yaml",
        )
    with pytest.raises(MethodError, match="not allowed"):
        parse_profile(
            {"methods": [{"method": "debug_traceBlockByNumber"}]},
            source="t.yaml",
        )
