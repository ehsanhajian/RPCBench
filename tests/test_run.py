from __future__ import annotations

import json
import math

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
    assert "1/1" in text
    assert "100%" in text
    assert "connection=" in text
    assert "Summary" in text
    assert "Ranking" in text
    assert "Fastest" in text
    assert "Capabilities" not in text
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
    assert stats.jitter_ms == pytest.approx(math.sqrt(200.0))
    assert dict(stats.by_class) == {"timeout": 1}
    assert dict(stats.histogram) == {
        "<50ms": 2,
        "<100ms": 0,
        "<250ms": 0,
        "<1s": 0,
        "≥1s": 0,
    }


def test_summarize_all_fail() -> None:
    stats = summarize((_fail(5.0), _fail(8.0)))
    assert stats.n_ok == 0
    assert stats.n_fail == 2
    assert stats.error_rate == 1.0
    assert stats.min_ms is None
    assert stats.p50_ms is None
    assert stats.p95_ms is None
    assert stats.p99_ms is None
    assert stats.jitter_ms is None
    assert dict(stats.histogram) == {
        "<50ms": 0,
        "<100ms": 0,
        "<250ms": 0,
        "<1s": 0,
        "≥1s": 0,
    }
    assert dict(stats.by_class) == {"timeout": 2}


def test_warmup_excluded_from_min_mean_max(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    # warmup 100ms (excluded), then 10 / 20 / 30 ms samples.
    # probe() ticks monotonic at start, headers, body, and parse-end per request.
    times = [
        0.0,
        0.050,
        0.100,
        0.100,
        0.100,
        0.105,
        0.110,
        0.110,
        0.110,
        0.120,
        0.130,
        0.130,
        0.130,
        0.145,
        0.160,
        0.160,
    ]
    clock = {"i": 0, "t": 0.160}

    def now() -> float:
        if clock["i"] < len(times):
            value = times[clock["i"]]
            clock["i"] += 1
            return value
        clock["t"] += 0.001
        return clock["t"]

    monkeypatch.setattr("rpcbench.rpc.time.monotonic", now)
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
    assert "p95=30.0ms" in text
    assert "mean=20.0ms" in text
    assert "30.0ms" in text
    assert "20.0ms" in text


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
    result = run_endpoints(
        cfg,
        method="eth_chainId",
        params=[],
        samples=1,
        warmup=0,
        budget=16,
        client=client,
    )
    assert seen[:1] == ["eth_chainId"]
    assert "eth_blockNumber" in seen
    assert "eth_getBlockByNumber" in seen
    assert "web3_clientVersion" in seen
    assert [hit.method for hit in result.outcomes[0].samples] == ["eth_chainId"]


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
    assert "50%" in text
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
    assert "100%" in text
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
    assert cfg.endpoints[0].url_id not in text
    full = format_run(result, verbose=True, color=False)
    assert secret not in full
    assert "tok_secret" not in full
    assert "[redacted]" in full
    assert cfg.endpoints[0].url_id not in full


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


def test_jitter_none_until_two_successes() -> None:
    one = summarize((_ok(10.0),))
    assert one.jitter_ms is None
    assert dict(one.histogram)["<50ms"] == 1
    two = summarize((_ok(10.0), _ok(10.0)))
    assert two.jitter_ms == pytest.approx(0.0)


def test_bimodal_histogram_splits_cache_and_miss() -> None:
    stats = summarize(tuple([_ok(20.0)] * 8 + [_ok(800.0)] * 8))
    assert dict(stats.histogram) == {
        "<50ms": 8,
        "<100ms": 0,
        "<250ms": 0,
        "<1s": 8,
        "≥1s": 0,
    }
    assert stats.min_ms == 20.0
    assert stats.max_ms == 800.0
    assert stats.jitter_ms is not None
    assert stats.jitter_ms > 300.0


def test_mix_runs_each_method_and_breaks_down() -> None:
    from rpcbench.methods import MIX_PROFILE
    from rpcbench.report import format_run

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload["method"])
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg,
        method="mix",
        samples=2,
        warmup=1,
        budget=32,
        client=client,
        workload=MIX_PROFILE,
        profile="mix",
    )
    assert result.profile == "mix"
    assert len(result.workload) == 6
    assert [spec.method for spec in MIX_PROFILE] == seen[:6]
    assert len(seen) == 23
    outcome = result.outcomes[0]
    assert outcome.stats.n_ok == 12
    assert [name for name, _ in outcome.by_method] == [s.name for s in MIX_PROFILE]
    assert all(stats.n_ok == 2 for _, stats in outcome.by_method)
    assert [pair.method for pair in result.pairs] == [
        spec.method for spec in MIX_PROFILE
    ] * 2
    text = format_run(result, color=False)
    assert "Method    mix" in text
    assert "Coverage  (active workload only" in text
    assert "Methods  (per-method; ranking uses the whole mix)" not in text
    full = format_run(result, verbose=True, color=False)
    assert "Methods  (per-method; ranking uses the whole mix)" in full
    assert "Coverage  (active workload only" in full
    assert "eth_getLogs" in full
    assert "eth_call" in full


def test_freshness_uses_first_blockNumber_sample() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["method"])
        height = "0x61" if str(request.url).endswith("/lag") else "0x64"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": height}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "tip", "url": "http://127.0.0.1:8545/tip"},
                {"name": "lag", "url": "http://127.0.0.1:8545/lag"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(cfg, samples=2, warmup=0, budget=16, client=client)
    assert seen.count("eth_blockNumber") == 4
    assert seen.count("eth_getBlockByNumber") == 8
    assert seen.count("web3_clientVersion") == 2
    assert result.cohort_height == 100
    by_name = {o.endpoint.name: o.freshness for o in result.outcomes}
    assert by_name["tip"] is not None and by_name["tip"].verdict == "fresh"
    assert by_name["tip"].lag_blocks == 0
    assert by_name["lag"] is not None and by_name["lag"].verdict == "stale"
    assert by_name["lag"].lag_blocks == 3
    assert by_name["lag"].lag_s == 36.0
    assert [len(o.samples) for o in result.outcomes] == [2, 2]


def test_extra_head_wave_stays_out_of_latency_stats() -> None:
    from rpcbench.methods import CallSpec

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = json.loads(request.content)["method"]
        seen.append(method)
        if method == "eth_blockNumber":
            height = "0x5f" if str(request.url).endswith("/lag") else "0x64"
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": height}
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x0"}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "tip", "url": "http://127.0.0.1:8545/tip"},
                {"name": "lag", "url": "http://127.0.0.1:8545/lag"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg,
        method="eth_getBalance",
        samples=1,
        warmup=0,
        budget=16,
        client=client,
        workload=(CallSpec("balance", "eth_getBalance", ()),),
        stale_blocks=2,
    )
    assert seen.count("eth_getBalance") == 2
    assert seen.count("eth_blockNumber") == 2
    assert seen.count("eth_getBlockByNumber") == 8
    assert seen.count("web3_clientVersion") == 2
    assert all(hit.method == "eth_getBalance" for o in result.outcomes for hit in o.samples)
    assert [o.stats.n_ok for o in result.outcomes] == [1, 1]
    by_name = {o.endpoint.name: o.freshness for o in result.outcomes}
    assert by_name["lag"] is not None and by_name["lag"].verdict == "stale"
    assert by_name["lag"].lag_blocks == 5


def test_stale_blocks_tolerance_is_configurable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        height = "0x5f" if str(request.url).endswith("/lag") else "0x64"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": height}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "tip", "url": "http://127.0.0.1:8545/tip"},
                {"name": "lag", "url": "http://127.0.0.1:8545/lag"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=8, client=client, stale_blocks=10
    )
    by_name = {o.endpoint.name: o.freshness for o in result.outcomes}
    assert by_name["lag"] is not None and by_name["lag"].lag_blocks == 5
    assert by_name["lag"].verdict == "fresh"
    text = format_run(result, color=False)
    assert "Stale" not in text.split("Ranking", 1)[0]


def test_mix_uses_chainId_block_time_without_extra_head() -> None:
    from rpcbench.methods import MIX_PROFILE

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = json.loads(request.content)["method"]
        seen.append(method)
        result = "0x89" if method == "eth_chainId" else "0x64"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": result}
        )

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg,
        method="mix",
        samples=1,
        warmup=0,
        budget=16,
        client=client,
        workload=MIX_PROFILE,
        profile="mix",
    )
    assert len(seen) == 11
    assert seen.count("eth_blockNumber") == 1
    assert seen.count("eth_getBlockByNumber") == 5
    assert result.block_time_s == 2.0
    assert result.outcomes[0].freshness is not None
    assert result.outcomes[0].freshness.height == 100
    assert result.outcomes[0].freshness.verdict == "fresh"


HASH_A = "0x" + "aa" * 32
HASH_B = "0x" + "bb" * 32


def _block_result(height: int, digest: str) -> dict[str, str]:
    return {"number": hex(height), "hash": digest}


def test_hash_wave_pins_cohort_and_flags_disagree() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "0x64"}
            )
        if method == "eth_getBlockByNumber":
            tag = payload["params"][0]
            if tag in ("latest", "safe", "finalized"):
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": _block_result(100, HASH_A),
                    },
                )
            digest = HASH_B if str(request.url).endswith("/wrong") else HASH_A
            assert payload["params"] == ["0x64", False]
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": _block_result(100, digest),
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "right", "url": "http://127.0.0.1:8545/right"},
                {"name": "also", "url": "http://127.0.0.1:8545/also"},
                {"name": "wrong", "url": "http://127.0.0.1:8545/wrong"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(cfg, samples=1, warmup=0, budget=16, client=client)
    assert result.pin_height == 100
    assert result.canonical_hash == HASH_A
    by_name = {o.endpoint.name: o.consistency for o in result.outcomes}
    assert by_name["right"] is not None and by_name["right"].verdict == "agree"
    assert by_name["also"] is not None and by_name["also"].verdict == "agree"
    assert by_name["wrong"] is not None and by_name["wrong"].verdict == "disagree"
    assert [len(o.samples) for o in result.outcomes] == [1, 1, 1]
    text = format_run(result, color=False)
    assert "Disagree 1/3    wrong" in text
    assert "wrong" not in text.split("Summary", 1)[1].split("Failed", 1)[0]


def test_block_pin_overrides_cohort_head() -> None:
    pins: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_blockNumber":
            height = "0x64" if str(request.url).endswith("/tip") else "0x63"
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": height}
            )
        if method == "eth_getBlockByNumber":
            pins.append(payload["params"][0])
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": _block_result(16, HASH_A),
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "tip", "url": "http://127.0.0.1:8545/tip"},
                {"name": "lag", "url": "http://127.0.0.1:8545/lag"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=16, client=client, block_pin=16
    )
    assert pins[:2] == ["0x10", "0x10"]
    assert pins.count("latest") == 2
    assert result.pin_height == 16
    assert result.cohort_height == 100
    assert {o.consistency.verdict for o in result.outcomes} == {"agree"}


def test_missing_block_hash_is_unknown_not_disagree() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_getBlockByNumber":
            result = None if str(request.url).endswith("/miss") else _block_result(
                100, HASH_A
            )
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": result}
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x64"}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "ok", "url": "http://127.0.0.1:8545/ok"},
                {"name": "miss", "url": "http://127.0.0.1:8545/miss"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(cfg, samples=1, warmup=0, budget=16, client=client)
    by_name = {o.endpoint.name: o.consistency for o in result.outcomes}
    assert by_name["ok"] is not None and by_name["ok"].verdict == "agree"
    assert by_name["miss"] is not None and by_name["miss"].verdict == "unknown"
    text = format_run(result, color=False)
    assert "Disagree" not in text.split("Ranking", 1)[0]


def test_client_label_and_tag_snapshots_are_not_ranked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "web3_clientVersion":
            label = (
                "Geth/v1.14.12-stable"
                if str(request.url).endswith("/geth")
                else "erigon/2.60.0"
            )
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": label}
            )
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "0x64"}
            )
        if method == "eth_getBlockByNumber":
            tag = payload["params"][0]
            if tag == "finalized" and str(request.url).endswith("/erigon"):
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32602, "message": "Unknown block tag"},
                    },
                )
            height = {"latest": "0x64", "safe": "0x62", "finalized": "0x60"}.get(
                tag, tag
            )
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"number": height, "hash": HASH_A},
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "geth", "url": "http://127.0.0.1:8545/geth"},
                {"name": "erigon", "url": "http://127.0.0.1:8545/erigon"},
            ]
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(cfg, samples=1, warmup=0, budget=32, client=client)
    by_name = {o.endpoint.name: o for o in result.outcomes}
    assert by_name["geth"].client == "Geth/v1.14.12-stable"
    assert by_name["erigon"].client == "erigon/2.60.0"
    assert [snap.tag for snap in by_name["geth"].tags] == [
        "latest",
        "safe",
        "finalized",
    ]
    assert by_name["geth"].tags[0].skipped is False
    assert by_name["geth"].tags[0].height == 100
    assert by_name["erigon"].tags[2].skipped is True
    assert by_name["erigon"].tags[2].skip_reason == "unsupported"
    assert all(hit.method == "eth_blockNumber" for o in result.outcomes for hit in o.samples)
    text = format_run(result, color=False)
    assert "Tags  (" not in text
    full = format_run(result, verbose=True, color=False)
    assert "Tags  (latest / safe / finalized snapshot; not mixed into ranking)" in full
    assert "Geth/v1.14.12-stable" in full
    assert "unsupported" in full
    assert "severity" not in text.lower()
    assert "finding" not in text.lower()
    assert "disclosed" not in text.lower()
    assert "cve" not in text.lower()


def test_burst_overlaps_existing_samples_and_splits_phases() -> None:
    import threading
    import time

    inflight = {"n": 0, "max": 0}
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            inflight["n"] += 1
            inflight["max"] = max(inflight["max"], inflight["n"])
        time.sleep(0.04)
        with lock:
            inflight["n"] -= 1
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg, samples=5, warmup=0, budget=32, burst=3, client=client
    )
    outcome = result.outcomes[0]
    assert result.burst == 3
    assert inflight["max"] == 3
    assert len(outcome.samples) == 5
    assert outcome.burst_stats is not None
    assert outcome.burst_stats.n_ok == 3
    assert outcome.steady_stats is not None
    assert outcome.steady_stats.n_ok == 2


def test_burst_does_not_add_requests() -> None:
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:1"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    plain = run_endpoints(
        cfg, samples=4, warmup=0, budget=32, client=client
    )
    n_plain = seen["n"]
    seen["n"] = 0
    burst = run_endpoints(
        cfg, samples=4, warmup=0, budget=32, burst=3, client=client
    )
    assert seen["n"] == n_plain
    assert burst.outcomes[0].stats.n_ok == plain.outcomes[0].stats.n_ok


def test_rps_paces_steady_starts(monkeypatch) -> None:
    waits: list[float] = []

    def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("rpcbench.run.time.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:1"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg,
        samples=4,
        warmup=0,
        budget=32,
        burst=1,
        rps=5.0,
        mode="sequential",
        client=client,
    )
    assert result.rps == 5.0
    assert len(waits) == 2
    assert waits[0] == pytest.approx(0.2, abs=0.05)
    assert waits[1] == pytest.approx(0.2, abs=0.05)


def test_rate_limit_counts_in_stats() -> None:
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] <= 2:
            return httpx.Response(429, text="slow")
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:1"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_endpoints(
        cfg, samples=4, warmup=0, budget=32, burst=2, client=client
    )
    outcome = result.outcomes[0]
    assert dict(outcome.stats.by_class)["rate_limit"] == 2
    assert outcome.burst_stats is not None
    assert dict(outcome.burst_stats.by_class).get("rate_limit") == 2
    assert outcome.steady_stats is not None
    assert outcome.steady_stats.n_fail == 0
    text = format_run(result, color=False)
    assert "Burst  (" not in text
    assert "rate_limit=2" in text
    full = format_run(result, verbose=True, color=False)
    assert "Burst  (first 2 timed samples overlap" in full
    assert "rate_limit is 429 / CU throttle" in full
    assert "finding" not in text.lower()

