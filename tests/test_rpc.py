from __future__ import annotations

import httpx

from rpcbench.rpc import is_rate_limit_message, probe


def test_http_429_is_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hit = probe("http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0)
    assert hit.error_class == "rate_limit"
    assert hit.ok is False


def test_http_429_jsonrpc_body_keeps_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": 429,
                    "message": "Your app has exceeded its compute units",
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hit = probe("http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0)
    assert hit.error_class == "rate_limit"
    assert "compute units" in (hit.error or "")


def test_jsonrpc_cu_throttle_is_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32005, "message": "Too Many Requests"},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hit = probe("http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0)
    assert hit.error_class == "rate_limit"
    assert hit.error == "Too Many Requests"


def test_401_stays_http_4xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="no key")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hit = probe("http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0)
    assert hit.error_class == "http_4xx"


def test_method_not_found_stays_jsonrpc() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32601, "message": "Method not found"},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hit = probe("http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0)
    assert hit.error_class == "jsonrpc"


def test_rate_limit_markers_are_tight() -> None:
    assert is_rate_limit_message("Too Many Requests")
    assert is_rate_limit_message("CU limit exceeded")
    assert not is_rate_limit_message("Method not found")
    assert not is_rate_limit_message("gas limit")
    assert not is_rate_limit_message("HTTP 401")
