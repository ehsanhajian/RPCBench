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
    family_workload,
    is_trace_method,
    resolve_workload,
    trace_block_params,
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


def test_core_catalogs_do_not_call_trace() -> None:
    for name in CORE_WORKLOADS:
        steps = family_workload("evm", name)
        assert not any(is_trace_method(spec.method) for spec in steps)
    tracing = {spec.name: spec for spec in family_workload("evm", "tracing")}
    assert tracing["trace"].method == "trace_block"
    assert tracing["trace"].optional is True
    assert tracing["trace"].params == trace_block_params()
    assert tracing["trace"].weight == 4
    assert "debug_" not in " ".join(
        spec.method for spec in family_workload("evm", "tracing")
    )


def test_tracing_mix_reports_latency() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        methods.append(method)
        if method == "trace_block":
            assert payload.get("params") == ["latest"]
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": []}
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
    assert "trace_block" in methods
    by_method = {name: stats for name, stats in result.outcomes[0].by_method}
    assert by_method["trace"].n_ok == 4
    assert by_method["trace"].p50_ms is not None
    assert by_method["trace"].p95_ms is not None
    text = format_run(result, color=False)
    assert "Methods  (per-method" in text
    assert "trace_block" in text
    data = run_to_dict(result)
    cell = data["coverage"]["providers"][0]["cells"]["trace"]
    assert cell["status"] == "ok"
    html = format_html(result)
    md = format_md(result)
    csv_row = next(csv.DictReader(io.StringIO(format_csv(result))))
    assert "trace_block" in html
    assert "## Trace" in md
    assert csv_row["trace_status"] == "ok"
    blob = (text + html + md).lower()
    assert "finding" not in blob
    assert "vulnerab" not in blob


def test_missing_trace_is_skip_not_finding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
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
    outcome = result.outcomes[0]
    spec = next(item for item in result.workload if item.name == "trace")
    cell = cell_for(outcome, spec, result)
    assert cell.status == "skip"
    assert cell.skip_reason == "unsupported"
    assert not is_coverage_miss(outcome, result)
    assert outcome.stats.n_fail == 0
    data = run_to_dict(result)
    row = data["coverage"]["providers"][0]["cells"]["trace"]
    assert row["status"] == "skip"
    assert row["skip_reason"] == "unsupported"
    assert data["ranking"][0]["rank"] == 1
    text = format_run(result, color=False)
    html = format_html(result)
    md = format_md(result)
    csv_row = next(csv.DictReader(io.StringIO(format_csv(result))))
    assert "skip/unsupported" in text
    assert "skip/unsupported" in html
    assert "skip/unsupported" in md
    assert csv_row["trace_status"] == "skip/unsupported"
    blob = (text + html + md).lower()
    assert "finding" not in blob
    assert "vulnerab" not in blob
    assert "severity" not in blob


def test_restricted_and_timeout_trace_are_skip() -> None:
    def restricted(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "trace_block":
            return httpx.Response(403, text="HTTP 403 forbidden")
        if payload["method"] == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)}
            )
        if payload["method"] == "eth_getBlockByNumber":
            return _ok_block(1000)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    steps = family_workload("evm", "tracing")
    denied = run_endpoints(
        _cfg(),
        method="tracing",
        samples=1,
        warmup=0,
        budget=64,
        client=httpx.Client(transport=httpx.MockTransport(restricted)),
        workload=steps,
        profile="tracing",
    )
    spec = next(item for item in denied.workload if item.name == "trace")
    cell = cell_for(denied.outcomes[0], spec, denied)
    assert cell.skip_reason == "restricted"
    assert denied.outcomes[0].stats.n_fail == 0
    assert run_to_dict(denied)["ranking"][0]["rank"] == 1

    def timed_out(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "trace_block":
            raise httpx.TimeoutException("took too long")
        if payload["method"] == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)}
            )
        if payload["method"] == "eth_getBlockByNumber":
            return _ok_block(1000)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    slow = run_endpoints(
        _cfg(),
        method="tracing",
        samples=1,
        warmup=0,
        budget=64,
        client=httpx.Client(transport=httpx.MockTransport(timed_out)),
        workload=steps,
        profile="tracing",
    )
    spec = next(item for item in slow.workload if item.name == "trace")
    cell = cell_for(slow.outcomes[0], spec, slow)
    assert cell.skip_reason == "timeout"
    assert slow.outcomes[0].stats.n_fail == 0
    assert run_to_dict(slow)["ranking"][0]["rank"] == 1


def test_yaml_allows_trace_block_rejects_filter_and_debug(tmp_path) -> None:
    path = tmp_path / "t.yaml"
    path.write_text(
        "name: indexer-trace\n"
        "methods:\n"
        "  - method: eth_getLogs\n"
        "  - method: trace_block\n"
        "    optional: false\n",
        encoding="utf-8",
    )
    plan = resolve_workload(
        profile=str(path), method=None, preset=None, params_json=None
    )
    by_name = {spec.name: spec for spec in plan.steps}
    assert by_name["trace"].optional is True
    assert by_name["trace"].params == trace_block_params()
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


def test_cli_tracing(tmp_path, monkeypatch, capsys) -> None:
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
        if method == "trace_block":
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
            "--workload",
            "tracing",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "trace_block" in methods
    assert "Method    tracing" in out
    assert "skip/unsupported" in out
    assert "finding" not in out.lower()
    assert not any(m.startswith("debug_") for m in methods)
    assert "trace_filter" not in methods
