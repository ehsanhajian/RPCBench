from __future__ import annotations

import json
from pathlib import Path

import pytest

from rpcbench.config import Endpoint
from rpcbench.report import format_json, format_run, run_to_dict
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, summarize


def _ok(ms: float) -> ProbeResult:
    return ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=ms,
        result="0x1",
        error=None,
        error_class=None,
        attempts=1,
    )


def _fail(error_class: str = "timeout", error: str = "took too long") -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=False,
        latency_ms=5.0,
        result=None,
        error=error,
        error_class=error_class,
        attempts=1,
    )


def _outcome(name: str, samples: tuple[ProbeResult, ...]) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name=name, url=f"http://127.0.0.1/{name}"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
    )


def _sample_result() -> RunResult:
    return RunResult(
        method="eth_blockNumber",
        params=(),
        samples=2,
        warmup=0,
        timeout=10.0,
        budget=32,
        outcomes=(
            _outcome("slow", (_ok(40.0), _ok(50.0))),
            _outcome("fast", (_ok(10.0), _ok(12.0))),
            _outcome("dead", (_fail(),)),
        ),
        budget_remaining=20,
    )


def test_json_report_matches_fixture() -> None:
    path = Path(__file__).resolve().parent / "fixtures" / "report.json"
    got = json.loads(format_json(_sample_result()))
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert got == expected


def test_json_schema_has_performance_capability_ranking_reliability() -> None:
    data = run_to_dict(_sample_result())
    assert data["schema"] == 1
    assert data["mode"] == "paired"
    assert data["seed"] == 0
    assert data["sequence_id"] == ""
    assert data["pairs"] == []
    assert data["concurrency"] == 0
    assert data["rank_by"] == "p95"
    assert data["ranking"][0]["rank_by"] == "p95"
    assert data["ranking"][0]["rank_value"] == 12.0
    assert data["tool"] == "rpcbench"
    assert data["summary"]["fastest"] == "fast"
    assert data["summary"]["failed_names"] == ["dead"]
    assert [row["name"] for row in data["ranking"]] == ["fast", "slow", "dead"]
    assert data["ranking"][0]["rank"] == 1
    assert data["ranking"][2]["rank"] is None
    assert [row["name"] for row in data["comparison"]] == ["slow", "fast", "dead"]
    assert data["comparison"][0]["capability"]["responded"] is True
    assert data["comparison"][2]["ok"] is False
    assert data["comparison"][2]["capability"]["error_class"] == "timeout"
    assert data["comparison"][0]["p50_ms"] == 40.0
    assert data["comparison"][1]["rps"] == 1000.0 / 11.0
    fast = data["providers"][0]
    assert fast["name"] == "fast"
    assert fast["url"] == "http://127.0.0.1/fast"
    assert fast["id"]
    assert "secret" not in json.dumps(data)
    perf = fast["performance"]
    assert perf["mean_ms"] == 11.0
    assert perf["p50_ms"] == 10.0
    assert perf["p95_ms"] == 12.0
    assert perf["jitter_ms"] == pytest.approx(2.0 ** 0.5)
    assert perf["rps"] == 1000.0 / 11.0
    assert perf["histogram"][0] == {"label": "<50ms", "lt_ms": 50.0, "n": 2}
    assert data["histogram_buckets"][0] == {"label": "<50ms", "lt_ms": 50.0}
    assert data["histogram_buckets"][-1] == {"label": "≥1s", "lt_ms": None}
    assert data["comparison"][1]["histogram"][0]["n"] == 2
    assert fast["reliability"]["score"] == 1.0
    assert fast["capability"]["responded"] is True
    assert data["capabilities"]["responded"] == 2
    assert data["capabilities"]["missed"] == [
        {"name": "dead", "error_class": "timeout"}
    ]


def test_json_is_enough_to_rebuild_cli_summary() -> None:
    result = _sample_result()
    data = run_to_dict(result)
    text = format_run(result, color=False)
    assert f"Fastest  {data['summary']['fastest']}" in text
    assert "Failed   1/3    dead" in text
    assert data["method"] in text
    ranking_block = text.split("Ranking", 1)[1].split("Providers", 1)[0]
    ranking_names = [row["name"] for row in data["ranking"]]
    pos = [ranking_block.index(name) for name in ranking_names]
    assert pos == sorted(pos)
    assert data["capabilities"]["method"] in text
    assert "missed     dead (timeout)" in text


def test_json_redacts_url_secrets() -> None:
    secret = "query_secret"
    outcome = EndpointOutcome(
        endpoint=Endpoint(
            name="paid",
            url="https://rpc.example/v3/abcdabcdabcdabcdabcdabcdabcdabcd"
            f"?apiKey={secret}",
        ),
        warmup=(),
        samples=(_ok(10.0),),
        stats=summarize((_ok(10.0),)),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=1,
        warmup=0,
        timeout=10.0,
        budget=8,
        outcomes=(outcome,),
        budget_remaining=7,
    )
    blob = format_json(result)
    assert secret not in blob
    assert "abcdabcdabcdabcdabcdabcdabcdabcd" not in blob
    assert "[redacted]" in blob
    data = json.loads(blob)
    assert data["providers"][0]["id"] == outcome.endpoint.url_id


def test_json_includes_seed_sequence_and_pairs() -> None:
    import httpx

    from rpcbench.config import parse_endpoints
    from rpcbench.run import make_sequence_id, run_endpoints

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "a", "url": "http://127.0.0.1:1"},
                {"name": "b", "url": "http://127.0.0.1:2"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg, samples=2, warmup=0, budget=8, seed=11, client=client
    )
    data = run_to_dict(result)
    assert data["mode"] == "paired"
    assert data["seed"] == 11
    assert data["sequence_id"] == make_sequence_id(
        seed=11, method="eth_blockNumber", params=[], warmup=0, samples=2
    )
    assert len(data["pairs"]) == 2
    assert data["pairs"][0]["kind"] == "sample"
    assert data["pairs"][0]["index"] == 0
    assert data["pairs"][0]["bodies"]["a"]
    assert data["pairs"][0]["bodies"]["a"] == data["pairs"][0]["bodies"]["b"]


def test_json_bimodal_histogram() -> None:
    samples = tuple([_ok(20.0)] * 8 + [_ok(1500.0)] * 8)
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=16,
        warmup=0,
        timeout=10.0,
        budget=32,
        outcomes=(_outcome("spiky", samples),),
        budget_remaining=16,
    )
    data = run_to_dict(result)
    hist = {row["label"]: row["n"] for row in data["providers"][0]["performance"]["histogram"]}
    assert hist == {
        "<50ms": 8,
        "<100ms": 0,
        "<250ms": 0,
        "<1s": 0,
        "≥1s": 8,
    }
    assert data["providers"][0]["performance"]["jitter_ms"] is not None
