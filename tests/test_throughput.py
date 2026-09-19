from __future__ import annotations

import csv
import io
import json
import time

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.markdown import format_md
from rpcbench.report import format_run, run_to_dict, throughput_status_label
from rpcbench.run import run_endpoints


def _ok(request: httpx.Request, *, delay: float = 0.0) -> httpx.Response:
    if delay:
        time.sleep(delay)
    payload = json.loads(request.content)
    ident = payload.get("id", 1) if isinstance(payload, dict) else 1
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": ident, "result": "0x1"},
    )


def _client(delay: float = 0.0) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(request, delay=delay)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_throughput_reports_successful_rps_duration_and_count() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=64,
        throughput=4,
        client=_client(delay=0.03),
    )
    assert result.throughput == 4
    summary = result.outcomes[0].throughput
    assert summary is not None
    assert summary.size == 4
    assert summary.method == "eth_blockNumber"
    assert summary.n_ok == 4
    assert summary.n_fail == 0
    assert summary.n == 4
    assert summary.duration_ms is not None
    assert summary.duration_ms >= 100
    assert summary.rps is not None
    assert summary.rps == pytest.approx(4000.0 / summary.duration_ms, rel=0.05)
    compact = format_run(result, color=False)
    assert "throughput=4" in compact
    table = compact.split("Throughput", 1)[1]
    assert "rps" in table
    assert "duration" in table
    assert "4/4" in table
    assert "finding" not in compact.lower()
    data = run_to_dict(result)
    assert data["throughput"] == 4
    blob = data["ranking"][0]["throughput"]
    assert blob["n_ok"] == 4
    assert blob["n"] == 4
    assert blob["rps"] is not None
    assert blob["duration_ms"] is not None
    html = format_html(result)
    assert ">Throughput</h2>" in html
    assert "not mixed into ranking" in html
    md = format_md(result)
    assert "## Throughput" in md
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["throughput_status"] == "ok"
    assert int(rows[0]["throughput_n_ok"]) == 4
    assert int(rows[0]["throughput_n"]) == 4
    assert float(rows[0]["throughput_rps"]) > 0
    assert float(rows[0]["throughput_duration_ms"]) > 0


def test_throughput_honors_rps_cap(monkeypatch) -> None:
    waits: list[float] = []

    def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("rpcbench.run.time.sleep", fake_sleep)
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=64,
        throughput=4,
        rps=5.0,
        client=_client(),
    )
    assert result.rps == 5.0
    summary = result.outcomes[0].throughput
    assert summary is not None
    assert summary.n == 4
    assert len(waits) == 3
    assert waits[0] == pytest.approx(0.2, abs=0.05)
    assert waits[1] == pytest.approx(0.2, abs=0.05)
    assert waits[2] == pytest.approx(0.2, abs=0.05)


def test_throughput_counts_429_as_rejected_not_crash() -> None:
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if isinstance(payload, dict) and payload.get("method") == "eth_blockNumber":
            seen["n"] += 1
            if seen["n"] in {2, 3}:
                return httpx.Response(429, text="too many requests")
        return _ok(request)

    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=64,
        throughput=4,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    summary = result.outcomes[0].throughput
    assert summary is not None
    assert summary.n_fail >= 1
    assert summary.rate_limit >= 1
    assert throughput_status_label(summary) == "ok"
    compact = format_run(result, color=False)
    table = compact.split("Throughput", 1)[1]
    assert str(summary.n_fail) in table
    assert "finding" not in compact.lower()
    html = format_html(result)
    assert "finding" not in html.lower()


def test_throughput_adds_n_requests_not_mixed_into_ranking() -> None:
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return _ok(request)

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:1"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    plain = run_endpoints(cfg, samples=1, warmup=0, budget=64, client=client)
    used_plain = n["i"]
    n["i"] = 0
    measured = run_endpoints(
        cfg, samples=1, warmup=0, budget=64, throughput=4, client=client
    )
    assert n["i"] - used_plain == 4
    assert plain.outcomes[0].stats.n_ok == measured.outcomes[0].stats.n_ok
    assert measured.outcomes[0].throughput is not None
    assert plain.outcomes[0].throughput is None


def test_throughput_off_omits_section() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=32, client=_client()
    )
    assert result.throughput == 0
    assert result.outcomes[0].throughput is None
    full = format_run(result, verbose=True, color=False)
    assert "Throughput  (" not in full
    assert "throughput=" not in full
    html = format_html(result)
    assert ">Throughput</h2>" not in html
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["throughput_status"] == ""
    assert rows[0]["throughput_rps"] == ""
    data = run_to_dict(result)
    assert data["throughput"] == 0
    assert data["ranking"][0]["throughput"] is None


def test_throughput_table_is_in_compact_cli() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=64, throughput=4, client=_client()
    )
    compact = format_run(result, verbose=False, color=False)
    assert "Throughput  (" in compact
    table = compact.split("Throughput", 1)[1]
    assert "status" in table
    assert "ok" in table
    assert "Providers" not in compact


def test_throughput_budget_miss_is_skip() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=6, throughput=4, client=_client()
    )
    summary = result.outcomes[0].throughput
    assert summary is not None
    assert summary.error_class == "budget"
    assert summary.n_ok == 0
    assert throughput_status_label(summary) == "skip"
    compact = format_run(result, color=False)
    table = compact.split("Throughput", 1)[1]
    assert "skip" in table
    assert "budget" in table
    html = format_html(result)
    assert "skip" in html
