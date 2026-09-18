from __future__ import annotations

import csv
import io
import json
import threading
import time

import httpx

from rpcbench.config import parse_endpoints
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.markdown import format_md
from rpcbench.report import format_run, inflight_status_label, run_to_dict
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


def test_concurrency_overlaps_and_compares_serial() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=64,
        inflight=4,
        client=_client(delay=0.05),
    )
    assert result.inflight == 4
    summary = result.outcomes[0].inflight
    assert summary is not None
    assert summary.size == 4
    assert summary.method == "eth_blockNumber"
    assert summary.n_ok == 4
    assert summary.n_fail == 0
    assert summary.concurrent_p50_ms is not None
    assert summary.concurrent_p95_ms is not None
    assert summary.serial_p50_ms is not None
    assert summary.concurrent_ms is not None
    assert summary.serial_ms is not None
    assert summary.concurrent_ms * 2 < summary.serial_ms
    compact = format_run(result, color=False)
    assert "inflight=4" in compact
    table = compact.split("Concurrency", 1)[1]
    assert "p50" in table
    assert "err" in table
    assert "ok" in table
    assert "finding" not in compact.lower()
    data = run_to_dict(result)
    assert data["inflight"] == 4
    blob = data["ranking"][0]["inflight"]
    assert blob["n_ok"] == 4
    assert blob["n_fail"] == 0
    assert blob["concurrent_p50_ms"] is not None
    html = format_html(result)
    assert ">Concurrency</h2>" in html
    assert "not mixed into ranking" in html
    md = format_md(result)
    assert "## Concurrency" in md
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["inflight_status"] == "ok"
    assert int(rows[0]["inflight_n_ok"]) == 4
    assert int(rows[0]["inflight_n_fail"]) == 0
    assert float(rows[0]["inflight_p50_ms"]) > 0


def test_concurrency_reports_error_count() -> None:
    lock = threading.Lock()
    current = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            current["n"] += 1
        try:
            time.sleep(0.03)
            with lock:
                overlapping = current["n"] >= 2
            if overlapping:
                return httpx.Response(429, text="too many requests")
            return _ok(request)
        finally:
            with lock:
                current["n"] -= 1

    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=64,
        inflight=4,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    summary = result.outcomes[0].inflight
    assert summary is not None
    assert summary.n_fail >= 1
    compact = format_run(result, color=False)
    table = compact.split("Concurrency", 1)[1].split("\n\n", 1)[0]
    assert str(summary.n_fail) in table
    data = run_to_dict(result)
    assert data["ranking"][0]["inflight"]["n_fail"] == summary.n_fail


def test_concurrency_adds_two_n_requests_not_mixed_into_ranking() -> None:
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
    concurrent = run_endpoints(
        cfg, samples=1, warmup=0, budget=64, inflight=4, client=client
    )
    assert n["i"] - used_plain == 8
    assert plain.outcomes[0].stats.n_ok == concurrent.outcomes[0].stats.n_ok
    assert concurrent.outcomes[0].inflight is not None
    assert plain.outcomes[0].inflight is None


def test_concurrency_off_omits_section() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=32, client=_client()
    )
    assert result.inflight == 0
    assert result.outcomes[0].inflight is None
    full = format_run(result, verbose=True, color=False)
    assert "Concurrency  (" not in full
    assert "inflight=" not in full
    html = format_html(result)
    assert ">Concurrency</h2>" not in html
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["inflight_status"] == ""
    assert rows[0]["inflight_p50_ms"] == ""
    data = run_to_dict(result)
    assert data["inflight"] == 0
    assert data["ranking"][0]["inflight"] is None


def test_concurrency_table_is_in_compact_cli() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=64, inflight=4, client=_client()
    )
    compact = format_run(result, verbose=False, color=False)
    assert "Concurrency  (" in compact
    table = compact.split("Concurrency", 1)[1]
    assert "status" in table
    assert "ok" in table
    assert "Providers" not in compact


def test_concurrency_budget_miss_is_skip() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=6, inflight=4, client=_client()
    )
    summary = result.outcomes[0].inflight
    assert summary is not None
    assert summary.error_class == "budget"
    assert summary.n_ok == 0
    assert inflight_status_label(summary) == "skip"
    compact = format_run(result, color=False)
    table = compact.split("Concurrency", 1)[1]
    assert "skip" in table
    assert "budget" in table
    html = format_html(result)
    assert "skip" in html
