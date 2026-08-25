"""Budgeted JSON-RPC HTTP client. Localhost and private URLs are allowed."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from rpcbench import __version__
from rpcbench.timing import (
    HttpTiming,
    TimingTransport,
    begin_timing,
    end_timing,
    snapshot_timing,
)

USER_AGENT = f"RPCBench/{__version__} (+https://github.com/ehsanhajian/RPCBench)"

# Reliability class, not a security finding. Tight on purpose: "limit" alone is too broad.
_RATE_LIMIT_MARKERS = (
    "too many requests",
    "rate limit",
    "ratelimit",
    "rate-limit",
    "request limit",
    "compute unit",
    "cu limit",
    "cu/s",
    "throughput limit",
    "capacity exceeded",
    "throttl",
)


class BudgetExceeded(RuntimeError):
    pass


def _invalid_url_reason(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        return str(exc)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return "URL must be http or https with a host"
    if "[" in parts.netloc and "]" not in parts.netloc:
        return "invalid host"
    return None


class RequestBudget:
    def __init__(self, max_requests: int) -> None:
        if max_requests < 1:
            raise ValueError("budget must be at least 1")
        self.max_requests = max_requests
        self.used = 0
        self._lock = threading.Lock()

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_requests - self.used)

    def consume(self) -> None:
        with self._lock:
            if self.used >= self.max_requests:
                raise BudgetExceeded(
                    f"request budget exceeded ({self.max_requests})"
                )
            self.used += 1


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reachable: bool
    latency_ms: float | None
    result: Any
    error: str | None
    error_class: str | None
    attempts: int
    body_hash: str | None = None
    method: str | None = None
    timing: HttpTiming | None = None


def make_client(*, timeout: float, new_connection: bool = False) -> httpx.Client:
    limits = (
        httpx.Limits(max_keepalive_connections=0, keepalive_expiry=0.0)
        if new_connection
        else httpx.Limits()
    )
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        transport=TimingTransport(limits=limits),
    )


def probe(
    url: str,
    method: str,
    *,
    params: list[Any] | None = None,
    timeout: float = 10.0,
    retries: int = 2,
    budget: RequestBudget | None = None,
    client: httpx.Client | None = None,
    headers: Sequence[tuple[str, str]] | None = None,
) -> ProbeResult:
    """Hit one JSON-RPC method. Transport failures retry; JSON-RPC errors do not."""
    owns = client is None
    reason = _invalid_url_reason(url)
    if reason:
        return ProbeResult(
            ok=False,
            reachable=False,
            latency_ms=None,
            result=None,
            error=reason,
            error_class="invalid_url",
            attempts=0,
        )
    extra = dict(headers or ())
    http = client or make_client(timeout=timeout)
    attempts = 0
    last_error = "unknown error"
    last_class = "error"
    last_latency: float | None = None
    last_timing: HttpTiming | None = None
    try:
        max_tries = max(1, retries + 1)
        for attempt in range(max_tries):
            attempts = attempt + 1
            if budget is not None:
                try:
                    budget.consume()
                except BudgetExceeded as exc:
                    return ProbeResult(
                        ok=False,
                        reachable=False,
                        latency_ms=None,
                        result=None,
                        error=str(exc),
                        error_class="budget",
                        attempts=attempts - 1,
                    )
            scratch = begin_timing()
            started = time.monotonic()
            headers_at: float | None = None
            body_at: float | None = None
            parse_ms: float | None = None
            raw: bytes | None = None
            response: httpx.Response | None = None
            try:
                request = http.build_request(
                    "POST",
                    url,
                    json={
                        "jsonrpc": "2.0",
                        "id": attempts,
                        "method": method,
                        "params": params or [],
                    },
                    headers=extra or None,
                )
                response = http.send(request, stream=True)
                headers_at = time.monotonic()
                raw = response.read()
                body_at = time.monotonic()
            except httpx.InvalidURL as exc:
                end_timing()
                return ProbeResult(
                    ok=False,
                    reachable=False,
                    latency_ms=None,
                    result=None,
                    error=str(exc),
                    error_class="invalid_url",
                    attempts=attempts,
                )
            except httpx.TimeoutException as exc:
                last_error = str(exc) or "timeout"
                last_class = "timeout"
                last_latency = (time.monotonic() - started) * 1000
                last_timing = snapshot_timing(
                    scratch, started, headers_at, body_at, parse_ms
                )
                end_timing()
                continue
            except httpx.ConnectError as exc:
                last_error = str(exc) or "connection failed"
                last_class = "connection"
                last_latency = (time.monotonic() - started) * 1000
                last_timing = snapshot_timing(
                    scratch, started, headers_at, body_at, parse_ms
                )
                end_timing()
                continue
            except httpx.RequestError as exc:
                last_error = str(exc) or "request failed"
                last_class = "connection"
                last_latency = (time.monotonic() - started) * 1000
                last_timing = snapshot_timing(
                    scratch, started, headers_at, body_at, parse_ms
                )
                end_timing()
                continue
            finally:
                if response is not None:
                    response.close()
            assert body_at is not None and raw is not None and response is not None
            latency_ms = (body_at - started) * 1000
            if response.status_code >= 400:
                code = response.status_code
                body_msg = _jsonrpc_error_from_bytes(raw)
                error = body_msg or f"HTTP {code}"
                timing = snapshot_timing(
                    scratch, started, headers_at, body_at, parse_ms
                )
                end_timing()
                return ProbeResult(
                    ok=False,
                    reachable=True,
                    latency_ms=latency_ms,
                    result=None,
                    error=error,
                    error_class=_http_error_class(code, error),
                    attempts=attempts,
                    timing=timing,
                )
            parse_started = body_at
            try:
                payload = json.loads(raw)
            except ValueError:
                parse_ms = (time.monotonic() - parse_started) * 1000
                timing = snapshot_timing(
                    scratch, started, headers_at, body_at, parse_ms
                )
                end_timing()
                return ProbeResult(
                    ok=False,
                    reachable=True,
                    latency_ms=latency_ms,
                    result=None,
                    error="response is not JSON",
                    error_class="malformed",
                    attempts=attempts,
                    timing=timing,
                )
            parse_ms = (time.monotonic() - parse_started) * 1000
            timing = snapshot_timing(scratch, started, headers_at, body_at, parse_ms)
            end_timing()
            if not isinstance(payload, dict):
                return ProbeResult(
                    ok=False,
                    reachable=True,
                    latency_ms=latency_ms,
                    result=None,
                    error="JSON-RPC response is not an object",
                    error_class="malformed",
                    attempts=attempts,
                    timing=timing,
                )
            if payload.get("error"):
                message = _error_message(payload.get("error"))
                return ProbeResult(
                    ok=False,
                    reachable=True,
                    latency_ms=latency_ms,
                    result=None,
                    error=message,
                    error_class=(
                        "rate_limit" if is_rate_limit_message(message) else "jsonrpc"
                    ),
                    attempts=attempts,
                    timing=timing,
                )
            return ProbeResult(
                ok=True,
                reachable=True,
                latency_ms=latency_ms,
                result=payload.get("result"),
                error=None,
                error_class=None,
                attempts=attempts,
                body_hash=_body_hash(payload.get("result")),
                timing=timing,
            )
        return ProbeResult(
            ok=False,
            reachable=False,
            latency_ms=last_latency,
            result=None,
            error=last_error,
            error_class=last_class,
            attempts=attempts,
            timing=last_timing,
        )
    finally:
        if owns:
            http.close()


def is_rate_limit_message(text: str) -> bool:
    blob = text.lower()
    return any(marker in blob for marker in _RATE_LIMIT_MARKERS)


def _http_error_class(status: int, message: str) -> str:
    if status == 429 or is_rate_limit_message(message):
        return "rate_limit"
    if status < 500:
        return "http_4xx"
    return "http_5xx"


def _error_message(err: Any) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err)


def _jsonrpc_error_from_bytes(raw: bytes) -> str | None:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict) or not payload.get("error"):
        return None
    return _error_message(payload.get("error"))


def _body_hash(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
