from __future__ import annotations

import json

import httpx

from rpcbench.config import Endpoint, parse_endpoints
from rpcbench.coverage import as_dict, cell_for, coverage_steps, is_not_offered
from rpcbench.methods import MIX_PROFILE, PRESETS, WORKLOADS, _WRITE_PREFIXES
from rpcbench.report import format_run, place_outcomes, run_to_dict
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, run_endpoints, summarize


_PRIVILEGED = (
    "admin_",
    "personal_",
    "miner_",
    "engine_",
    "txpool_",
    "clique_",
    "rpc_modules",
    "rpc_methods",
    "eth_accounts",
    "validatorExit",
    "adv_",
)


def _ok(ms: float, method: str = "eth_blockNumber") -> ProbeResult:
    return ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=ms,
        result="0x1",
        error=None,
        error_class=None,
        attempts=1,
        method=method,
    )


def _fail(
    error_class: str,
    error: str,
    method: str = "eth_getLogs",
) -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=True,
        latency_ms=12.0,
        result=None,
        error=error,
        error_class=error_class,
        attempts=1,
        method=method,
    )


def test_default_catalogs_have_no_privileged_namespace() -> None:
    methods = [spec.method for spec in MIX_PROFILE]
    methods.extend(name for name, _params in PRESETS.values())
    for family in WORKLOADS.values():
        for steps in family.values():
            methods.extend(spec.method for spec in steps)
    blob = " ".join(methods).lower()
    for marker in _PRIVILEGED:
        assert marker.lower() not in blob
    for method in methods:
        lower = method.lower()
        assert not any(lower.startswith(p) for p in _WRITE_PREFIXES)
        assert not lower.startswith("trace_")
        assert not lower.startswith("debug_")


def test_coverage_steps_are_the_active_mix_only() -> None:
    outcome = EndpointOutcome(
        endpoint=Endpoint(name="a", url="http://127.0.0.1/a"),
        warmup=(),
        samples=(),
        stats=summarize(()),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=1,
        warmup=0,
        timeout=5.0,
        budget=8,
        outcomes=(outcome,),
        budget_remaining=7,
        profile="mix",
        workload=MIX_PROFILE,
    )
    steps = coverage_steps(result)
    assert [spec.method for spec in steps] == [spec.method for spec in MIX_PROFILE]
    assert "eth_getLogs" in {spec.method for spec in steps}
    assert "admin_peers" not in {spec.method for spec in steps}


def test_getLogs_jsonrpc_is_coverage_miss_not_vuln() -> None:
    rows = []
    hits: list[ProbeResult] = []
    for spec in MIX_PROFILE:
        if spec.name == "logs":
            chunk = (
                _fail("jsonrpc", "filter not supported", spec.method),
                _fail("jsonrpc", "filter not supported", spec.method),
            )
        else:
            chunk = (_ok(10.0, spec.method), _ok(11.0, spec.method))
        hits.extend(chunk)
        rows.append((spec.name, summarize(chunk)))
    outcome = EndpointOutcome(
        endpoint=Endpoint(name="indexer", url="http://127.0.0.1/ix"),
        warmup=(),
        samples=tuple(hits),
        stats=summarize(tuple(hits)),
        by_method=tuple(rows),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=2,
        warmup=0,
        timeout=5.0,
        budget=32,
        outcomes=(outcome,),
        budget_remaining=20,
        profile="mix",
        workload=MIX_PROFILE,
    )
    logs = next(spec for spec in MIX_PROFILE if spec.name == "logs")
    cell = cell_for(outcome, logs, result)
    assert cell.status == "miss"
    assert cell.error_class == "jsonrpc"
    data = as_dict(result)
    assert data["providers"][0]["missed"] == ["logs"]
    assert data["providers"][0]["cells"]["logs"]["method"] == "eth_getLogs"
    text = format_run(result, color=False)
    assert "Coverage" in text
    assert "jsonrpc" in text
    assert "miss=logs" in text
    assert "Miss     1/1    indexer (logs)" in text
    assert "finding" not in text.lower()
    assert "vuln" not in text.lower()
    assert "severity" not in text.lower()
    placed = place_outcomes(result)
    assert placed[0].rank is None
    assert placed[0].reliable is False


def test_method_not_found_is_skip_not_offered() -> None:
    rows = []
    hits: list[ProbeResult] = []
    for spec in MIX_PROFILE:
        if spec.name == "logs":
            chunk = (_fail("jsonrpc", "Method not found", spec.method),)
        else:
            chunk = (_ok(10.0, spec.method),)
        hits.extend(chunk)
        rows.append((spec.name, summarize(chunk)))
    outcome = EndpointOutcome(
        endpoint=Endpoint(name="a", url="http://127.0.0.1/a"),
        warmup=(),
        samples=tuple(hits),
        stats=summarize(tuple(hits)),
        by_method=tuple(rows),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=1,
        warmup=0,
        timeout=5.0,
        budget=16,
        outcomes=(outcome,),
        budget_remaining=10,
        profile="mix",
        workload=MIX_PROFILE,
    )
    logs = next(spec for spec in MIX_PROFILE if spec.name == "logs")
    cell = cell_for(outcome, logs, result)
    assert cell.status == "skip"
    assert cell.label() == "skip"
    assert is_not_offered("the method eth_getLogs does not exist/is not available")


def test_live_mix_getLogs_error_is_coverage_miss() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "eth_getLogs":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32000, "message": "filter not supported"},
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )
    result = run_endpoints(
        cfg,
        method="mix",
        samples=1,
        warmup=0,
        budget=16,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        workload=MIX_PROFILE,
        profile="mix",
    )
    data = run_to_dict(result)
    logs = next(
        row for row in data["coverage"]["steps"] if row["method"] == "eth_getLogs"
    )
    cell = data["coverage"]["providers"][0]["cells"][logs["name"]]
    assert cell["status"] == "miss"
    assert cell["error_class"] == "jsonrpc"
    assert "logs" in data["coverage"]["providers"][0]["missed"]
    assert data["summary"]["coverage_miss_names"] == ["a"]
    blob = json.dumps(data).lower()
    assert "finding" not in blob
    assert "vuln" not in blob
    assert "admin_" not in json.dumps(data["coverage"])
