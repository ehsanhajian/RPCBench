from __future__ import annotations

import httpx

from rpcbench.config import parse_endpoints
from rpcbench.rpc import RequestBudget, probe
from rpcbench.run import format_run, run_endpoints


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
    result = run_endpoints(cfg, retries=0, budget=8, client=client)
    assert result.outcomes[0].probe.ok
    assert not result.outcomes[1].probe.ok
    assert result.outcomes[1].probe.error_class == "connection"
    text = format_run(result)
    assert "ok" in text
    assert "fail" in text
    assert "reachability" in text


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
    result = run_endpoints(cfg, retries=0, budget=1, client=client)
    assert result.outcomes[0].probe.ok
    assert result.outcomes[1].probe.error_class == "budget"


def test_request_budget_counts() -> None:
    purse = RequestBudget(2)
    purse.consume()
    purse.consume()
    assert purse.remaining == 0
