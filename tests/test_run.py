from __future__ import annotations

import json

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.rpc import ProbeResult, RequestBudget, probe
from rpcbench.report import format_run
from rpcbench.run import make_sequence_id, percentile, run_endpoints, summarize


def test_probe_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x10"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = probe("http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0)
    assert result.ok
    assert result.reachable
    assert result.result == "0x10"
    assert result.latency_ms is not None


def test_probe_timeout_does_not_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("took too long")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = probe(
        "http://127.0.0.1:8545",
        "eth_blockNumber",
        client=client,
        retries=1,
        timeout=0.1,
    )
    assert not result.ok
    assert result.error_class == "timeout"
    assert result.attempts == 2


def test_invalid_url_is_classified() -> None:
    result = probe("http://[", "eth_blockNumber", retries=0, timeout=1)
    assert not result.ok
    assert result.error_class == "invalid_url"


def test_run_continues_after_one_failure() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if str(request.url).endswith("/bad"):
            raise httpx.ConnectError("nope")
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "ok", "url": "http://127.0.0.1:8545/ok"},
                {"name": "bad", "url": "http://127.0.0.1:8545/bad"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(cfg, samples=1, warmup=0, budget=8, client=client)
    assert result.outcomes[0].stats.n_ok == 1
    assert result.outcomes[1].stats.n_ok == 0
    assert result.outcomes[1].samples[0].error_class == "connection"
    text = format_run(result, color=False)
    assert "ok" in text
    assert "fail" in text
    assert "min=" in text or "n=1/1" in text
    assert "err=0%" in text
    assert "err=100%" in text
    assert "connection=" in text
    assert "Summary" in text
    assert "Ranking" in text
    assert "Fastest" in text
    assert "Capabilities" in text
    assert "↳ Next:" not in text


def test_budget_skips_later_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
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
        cfg, samples=1, warmup=0, budget=1, mode="sequential", client=client
    )
    assert result.outcomes[0].stats.n_ok == 1
    assert result.outcomes[1].samples[-1].error_class == "budget"
    text = format_run(result, color=False)
    assert "Summary" in text
    assert "Ranking" in text
    assert "budget=" in text
    assert "a" in text
    assert "b" in text


def test_request_budget_counts() -> None:
    purse = RequestBudget(2)
    purse.consume()
    purse.consume()
    assert purse.remaining == 0


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


def _fail(
    ms: float, error_class: str = "timeout", error: str = "timeout"
) -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=False,
        latency_ms=ms,
        result=None,
        error=error,
        error_class=error_class,
        attempts=1,
    )


def test_summarize_min_mean_max_ignores_failures() -> None:
    stats = summarize((_ok(10.0), _fail(99.0), _ok(30.0)))
    assert stats.n_ok == 2
    assert stats.n_fail == 1
    assert stats.error_rate == pytest.approx(1 / 3)
    assert stats.min_ms == 10.0
    assert stats.mean_ms == 20.0
    assert stats.max_ms == 30.0
    assert dict(stats.by_class) == {"timeout": 1}


def test_summarize_all_fail() -> None:
    stats = summarize((_fail(5.0), _fail(8.0)))
    assert stats.n_ok == 0
    assert stats.n_fail == 2
    assert stats.error_rate == 1.0
    assert stats.min_ms is None
    assert stats.p50_ms is None
    assert stats.p95_ms is None
    assert stats.p99_ms is None
    assert dict(stats.by_class) == {"timeout": 2}


def test_warmup_excluded_from_min_mean_max(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    # warmup 100ms (excluded), then 10 / 20 / 30 ms samples
    marks = iter([0.0, 0.100, 0.100, 0.110, 0.110, 0.130, 0.130, 0.160])
    monkeypatch.setattr("rpcbench.rpc.time.monotonic", lambda: next(marks))
    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg, samples=3, warmup=1, budget=16, client=client
    )
    outcome = result.outcomes[0]
    assert len(outcome.warmup) == 1
    assert len(outcome.samples) == 3
    assert outcome.warmup[0].latency_ms == pytest.approx(100.0)
    assert outcome.stats.min_ms == pytest.approx(10.0)
    assert outcome.stats.mean_ms == pytest.approx(20.0)
    assert outcome.stats.max_ms == pytest.approx(30.0)
    assert outcome.stats.p50_ms == pytest.approx(20.0)
    assert outcome.stats.p95_ms == pytest.approx(30.0)
    assert outcome.stats.p99_ms == pytest.approx(30.0)
    text = format_run(result, color=False)
    assert "min=10.0ms" in text
    assert "mean=20.0ms" in text
    assert "max=30.0ms" in text
    assert "p50=20.0ms" in text
    assert "p95=30.0ms" in text
    assert "p99=30.0ms" in text
    assert "(n=3)" in text
    assert "err=0%" in text


def test_run_sends_configured_method() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["method"])
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    run_endpoints(
        cfg,
        method="eth_chainId",
        params=[],
        samples=1,
        warmup=0,
        budget=4,
        client=client,
    )
    assert seen == ["eth_chainId"]


def test_percentile_nearest_rank() -> None:
    vals = [float(i) for i in range(1, 21)]
    assert percentile(vals, 0.50) == 10.0
    assert percentile(vals, 0.95) == 19.0
    assert percentile(vals, 0.99) == 20.0
    assert percentile([7.0], 0.50) == 7.0
    assert percentile([7.0], 0.99) == 7.0


def test_summarize_percentiles_ignore_failures() -> None:
    ok = [_ok(float(i)) for i in range(1, 21)]
    stats = summarize(tuple(ok + [_fail(999.0), _fail(1.0)]))
    assert stats.n_ok == 20
    assert stats.n_fail == 2
    assert stats.p50_ms == 10.0
    assert stats.p95_ms == 19.0
    assert stats.p99_ms == 20.0
    assert stats.min_ms == 1.0
    assert stats.max_ms == 20.0
    assert stats.error_rate == pytest.approx(2 / 22)
    assert dict(stats.by_class) == {"timeout": 2}


def test_timeout_and_jsonrpc_counted_separately() -> None:
    stats = summarize(
        (
            _ok(10.0),
            _fail(20.0, "timeout", "took too long"),
            _fail(30.0, "jsonrpc", "Method not found"),
            _ok(40.0),
        )
    )
    assert stats.n_ok == 2
    assert stats.n_fail == 2
    assert stats.error_rate == pytest.approx(0.5)
    assert dict(stats.by_class) == {"timeout": 1, "jsonrpc": 1}


def test_mixed_timeouts_show_error_rate() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            raise httpx.TimeoutException("slow")
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(cfg, samples=4, warmup=0, budget=8, client=client)
    stats = result.outcomes[0].stats
    assert stats.n_ok == 2
    assert stats.n_fail == 2
    assert stats.error_rate == pytest.approx(0.5)
    assert dict(stats.by_class) == {"timeout": 2}
    text = format_run(result, color=False)
    assert "err=50%" in text
    assert "timeout=2" in text


def test_bad_url_run_is_100_percent_error() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "bad", "url": "http://["}]}
    )
    result = run_endpoints(
        cfg, samples=4, warmup=0, budget=8, mode="sequential"
    )
    stats = result.outcomes[0].stats
    assert stats.n_ok == 0
    assert stats.error_rate == 1.0
    assert dict(stats.by_class) == {"invalid_url": 1}
    text = format_run(result, color=False)
    assert "err=100%" in text
    assert "invalid_url=" in text


def test_probe_http_jsonrpc_and_malformed_classes() -> None:
    def http_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    client = httpx.Client(transport=httpx.MockTransport(http_handler))
    http_err = probe(
        "http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0
    )
    assert http_err.error_class == "http_5xx"

    def four_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="no")

    client = httpx.Client(transport=httpx.MockTransport(four_handler))
    four = probe(
        "http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0
    )
    assert four.error_class == "http_4xx"

    def rpc_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32601, "message": "Method not found"},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(rpc_handler))
    rpc_err = probe(
        "http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0
    )
    assert rpc_err.error_class == "jsonrpc"
    assert "Method not found" in (rpc_err.error or "")

    def bad_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client = httpx.Client(transport=httpx.MockTransport(bad_json))
    malformed = probe(
        "http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0
    )
    assert malformed.error_class == "malformed"


def test_probe_sends_headers_and_report_hides_them() -> None:
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    secret = "hdr_secret_value_xyz"
    url = "https://rpc.example/v3/abcdabcdabcdabcdabcdabcdabcdabcd?apiKey=query_secret"
    cfg = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "paid",
                    "url": url,
                    "bearer": "tok_secret",
                    "headers": {"X-Api-Key": secret},
                }
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(cfg, samples=1, warmup=0, budget=4, client=client)
    assert seen
    assert seen[0]["X-Api-Key"] == secret
    assert seen[0]["Authorization"] == "Bearer tok_secret"
    text = format_run(result, color=False)
    assert secret not in text
    assert "tok_secret" not in text
    assert "query_secret" not in text
    assert "abcdabcdabcdabcdabcdabcdabcdabcd" not in text
    assert "[redacted]" in text
    assert f"id={cfg.endpoints[0].url_id}" in text


def test_max_duration_skips_later_endpoints(monkeypatch) -> None:
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    monkeypatch.setattr("rpcbench.run.time.monotonic", now)

    def handler(request: httpx.Request) -> httpx.Response:
        clock["t"] = 5.0
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
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
        cfg,
        samples=1,
        warmup=0,
        budget=8,
        max_duration=1.0,
        mode="sequential",
        client=client,
    )
    assert result.outcomes[0].stats.n_ok == 1
    assert result.outcomes[1].samples[-1].error_class == "duration"
    text = format_run(result, color=False)
    assert "duration=" in text
    assert "Summary" in text


def test_probe_body_hash_is_stable() -> None:
    import hashlib

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x10"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = probe("http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0)
    second = probe("http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0)
    blob = json.dumps("0x10", sort_keys=True, default=str, separators=(",", ":"))
    expected = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
    assert first.body_hash == second.body_hash == expected


def test_paired_races_providers_per_sample() -> None:
    import threading

    inflight = {"n": 0, "max": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=2)

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            inflight["n"] += 1
            inflight["max"] = max(inflight["max"], inflight["n"])
        barrier.wait()
        with lock:
            inflight["n"] -= 1
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
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
        cfg, samples=2, warmup=0, budget=16, client=client
    )
    assert result.mode == "paired"
    assert inflight["max"] == 2
    assert len(result.pairs) == 2
    assert [len(o.samples) for o in result.outcomes] == [2, 2]


def test_sequential_does_not_overlap_providers() -> None:
    import threading
    import time

    inflight = {"n": 0, "max": 0}
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            inflight["n"] += 1
            inflight["max"] = max(inflight["max"], inflight["n"])
        time.sleep(0.03)
        with lock:
            inflight["n"] -= 1
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
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
        cfg, samples=2, warmup=0, budget=16, mode="sequential", client=client
    )
    assert result.mode == "sequential"
    assert inflight["max"] == 1
    assert result.pairs == ()


def test_sequence_id_stable_for_same_seed() -> None:
    kwargs = dict(
        seed=7, method="eth_blockNumber", params=[], warmup=1, samples=10
    )
    assert make_sequence_id(**kwargs) == make_sequence_id(**kwargs)
    assert make_sequence_id(**kwargs) != make_sequence_id(
        seed=8, method="eth_blockNumber", params=[], warmup=1, samples=10
    )
    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://["}]}
    )
    first = run_endpoints(cfg, samples=1, warmup=0, budget=4, seed=3)
    second = run_endpoints(cfg, samples=1, warmup=0, budget=4, seed=3)
    assert first.sequence_id == second.sequence_id
    assert first.sequence_id == make_sequence_id(
        seed=3, method="eth_blockNumber", params=[], warmup=0, samples=1
    )


def test_paired_counts_stay_aligned_when_one_side_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/bad"):
            raise httpx.ConnectError("nope")
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "ok", "url": "http://127.0.0.1:8545/ok"},
                {"name": "bad", "url": "http://127.0.0.1:8545/bad"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(cfg, samples=3, warmup=1, budget=16, client=client)
    assert [len(o.warmup) for o in result.outcomes] == [1, 1]
    assert [len(o.samples) for o in result.outcomes] == [3, 3]
    assert len(result.pairs) == 3
    assert result.outcomes[0].stats.n_ok == 3
    assert result.outcomes[1].stats.n_ok == 0
    bodies = dict(result.pairs[0].bodies)
    assert bodies["ok"]
    assert bodies["bad"] is None


def test_paired_budget_aligns_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
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
    result = run_endpoints(cfg, samples=1, warmup=0, budget=1, client=client)
    assert [len(o.samples) for o in result.outcomes] == [1, 1]
    classes = {o.samples[0].error_class for o in result.outcomes}
    oks = sum(1 for o in result.outcomes if o.stats.n_ok)
    assert oks == 1
    assert None in classes
    assert "budget" in classes


def test_paired_duration_skips_remaining_wave(monkeypatch) -> None:
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    monkeypatch.setattr("rpcbench.run.time.monotonic", now)

    def handler(request: httpx.Request) -> httpx.Response:
        clock["t"] = 5.0
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
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
        cfg, samples=2, warmup=0, budget=16, max_duration=1.0, client=client
    )
    assert [len(o.samples) for o in result.outcomes] == [2, 2]
    assert result.outcomes[0].samples[0].ok
    assert result.outcomes[1].samples[0].ok
    assert result.outcomes[0].samples[1].error_class == "duration"
    assert result.outcomes[1].samples[1].error_class == "duration"


def test_paired_invalid_url_keeps_sample_count() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "bad", "url": "http://["}]}
    )
    result = run_endpoints(cfg, samples=4, warmup=0, budget=8)
    stats = result.outcomes[0].stats
    assert stats.n_ok == 0
    assert stats.n_fail == 4
    assert dict(stats.by_class) == {"invalid_url": 4}

