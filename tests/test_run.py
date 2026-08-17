from __future__ import annotations

import json

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.rpc import ProbeResult, RequestBudget, probe
from rpcbench.run import format_run, run_endpoints, summarize


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
    text = format_run(result)
    assert "ok" in text
    assert "fail" in text
    assert "min=" in text or "n=1/1" in text


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
    result = run_endpoints(cfg, samples=1, warmup=0, budget=1, client=client)
    assert result.outcomes[0].stats.n_ok == 1
    assert result.outcomes[1].samples[-1].error_class == "budget"


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


def _fail(ms: float) -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=False,
        latency_ms=ms,
        result=None,
        error="timeout",
        error_class="timeout",
        attempts=1,
    )


def test_summarize_min_mean_max_ignores_failures() -> None:
    stats = summarize((_ok(10.0), _fail(99.0), _ok(30.0)))
    assert stats.n_ok == 2
    assert stats.n_fail == 1
    assert stats.min_ms == 10.0
    assert stats.mean_ms == 20.0
    assert stats.max_ms == 30.0


def test_summarize_all_fail() -> None:
    stats = summarize((_fail(5.0), _fail(8.0)))
    assert stats.n_ok == 0
    assert stats.n_fail == 2
    assert stats.min_ms is None


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
    text = format_run(result)
    assert "min=10.0ms" in text
    assert "mean=20.0ms" in text
    assert "max=30.0ms" in text


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
