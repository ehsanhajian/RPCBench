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

USER_AGENT = f"RPCBench/{__version__} (+https://github.com/ehsanhajian/RPCBench)"


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
    http = client or httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    attempts = 0
    last_error = "unknown error"
    last_class = "error"
    last_latency: float | None = None
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
            started = time.monotonic()
            try:
                response = http.post(
                    url,
                    json={
                        "jsonrpc": "2.0",
                        "id": attempts,
                        "method": method,
                        "params": params or [],
                    },
                    headers=extra or None,
                )
            except httpx.InvalidURL as exc:
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
                continue
            except httpx.ConnectError as exc:
                last_error = str(exc) or "connection failed"
                last_class = "connection"
                last_latency = (time.monotonic() - started) * 1000
                continue
            except httpx.RequestError as exc:
                last_error = str(exc) or "request failed"
                last_class = "connection"
                last_latency = (time.monotonic() - started) * 1000
                continue
            latency_ms = (time.monotonic() - started) * 1000
            if response.status_code >= 400:
                code = response.status_code
                http_class = "http_4xx" if code < 500 else "http_5xx"
                return ProbeResult(
                    ok=False,
                    reachable=True,
                    latency_ms=latency_ms,
                    result=None,
                    error=f"HTTP {code}",
                    error_class=http_class,
                    attempts=attempts,
                )
            try:
                payload = response.json()
            except ValueError:
                return ProbeResult(
                    ok=False,
                    reachable=True,
                    latency_ms=latency_ms,
                    result=None,
                    error="response is not JSON",
                    error_class="malformed",
                    attempts=attempts,
                )
            if not isinstance(payload, dict):
                return ProbeResult(
                    ok=False,
                    reachable=True,
                    latency_ms=latency_ms,
                    result=None,
                    error="JSON-RPC response is not an object",
                    error_class="malformed",
                    attempts=attempts,
                )
            if payload.get("error"):
                err = payload["error"]
                if isinstance(err, dict):
                    message = str(err.get("message") or err)
                else:
                    message = str(err)
                return ProbeResult(
                    ok=False,
                    reachable=True,
                    latency_ms=latency_ms,
                    result=None,
                    error=message,
                    error_class="jsonrpc",
                    attempts=attempts,
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
            )
        return ProbeResult(
            ok=False,
            reachable=False,
            latency_ms=last_latency,
            result=None,
            error=last_error,
            error_class=last_class,
            attempts=attempts,
        )
    finally:
        if owns:
            http.close()


def _body_hash(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
