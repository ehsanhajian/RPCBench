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
HTTP_1 = "1.1"
HTTP_2 = "2"

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
    http_version: str | None = None
    encoding: str | None = None
    bytes_out: int | None = None
    bytes_in: int | None = None


@dataclass(frozen=True)
class BatchProbeResult:
    """One JSON-RPC batch POST. Not mixed into ranking samples."""

    size: int
    supported: bool
    partial: bool
    ok: bool
    reachable: bool
    latency_ms: float | None
    n_ok: int
    n_fail: int
    error: str | None
    error_class: str | None
    attempts: int
    http_version: str | None = None
    encoding: str | None = None
    bytes_out: int | None = None
    bytes_in: int | None = None


def make_client(
    *, timeout: float, new_connection: bool = False, http2: bool = False
) -> httpx.Client:
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
            "Accept-Encoding": "gzip, deflate, br",
            "Content-Type": "application/json",
        },
        transport=TimingTransport(limits=limits, http2=http2),
    )


def normalize_http_version(raw: str | None) -> str | None:
    if not raw:
        return None
    blob = raw.upper().replace("HTTP/", "").strip()
    if blob.startswith("2"):
        return HTTP_2
    if blob.startswith("1.1"):
        return HTTP_1
    if blob.startswith("1"):
        return "1.0"
    return blob.lower()


def _content_encoding(response: httpx.Response | None) -> str | None:
    if response is None:
        return None
    raw = response.headers.get("content-encoding")
    if not raw:
        return None
    first = raw.split(",")[0].strip().lower()
    if first in {"", "identity"}:
        return None
    return first


def _bytes_out(request: httpx.Request | None) -> int | None:
    if request is None or request.content is None:
        return None
    return len(request.content)


def _bytes_in(response: httpx.Response | None) -> int | None:
    if response is None:
        return None
    raw = response.headers.get("content-length")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    return int(response.num_bytes_downloaded)


def _transport_fields(
    request: httpx.Request | None, response: httpx.Response | None
) -> dict[str, str | int | None]:
    version = None
    if response is not None:
        version = normalize_http_version(response.http_version)
    return {
        "http_version": version,
        "encoding": _content_encoding(response),
        "bytes_out": _bytes_out(request),
        "bytes_in": _bytes_in(response),
    }


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
    last_transport: dict[str, str | int | None] = {}
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
            request: httpx.Request | None = None
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
                    **_transport_fields(request, response),
                )
            except httpx.TimeoutException as exc:
                last_error = str(exc) or "timeout"
                last_class = "timeout"
                last_latency = (time.monotonic() - started) * 1000
                last_timing = snapshot_timing(
                    scratch, started, headers_at, body_at, parse_ms
                )
                last_transport = _transport_fields(request, response)
                end_timing()
                continue
            except httpx.ConnectError as exc:
                last_error = str(exc) or "connection failed"
                last_class = "connection"
                last_latency = (time.monotonic() - started) * 1000
                last_timing = snapshot_timing(
                    scratch, started, headers_at, body_at, parse_ms
                )
                last_transport = _transport_fields(request, response)
                end_timing()
                continue
            except httpx.RequestError as exc:
                last_error = str(exc) or "request failed"
                last_class = "connection"
                last_latency = (time.monotonic() - started) * 1000
                last_timing = snapshot_timing(
                    scratch, started, headers_at, body_at, parse_ms
                )
                last_transport = _transport_fields(request, response)
                end_timing()
                continue
            finally:
                if response is not None:
                    response.close()
            assert body_at is not None and raw is not None and response is not None
            latency_ms = (body_at - started) * 1000
            transport = _transport_fields(request, response)
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
                    **transport,
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
                    **transport,
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
                    **transport,
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
                    **transport,
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
                **transport,
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
            **last_transport,
        )
    finally:
        if owns:
            http.close()


def probe_batch(
    url: str,
    method: str,
    *,
    params: list[Any] | None = None,
    size: int = 3,
    timeout: float = 10.0,
    budget: RequestBudget | None = None,
    client: httpx.Client | None = None,
    headers: Sequence[tuple[str, str]] | None = None,
) -> BatchProbeResult:
    """POST a JSON-RPC array of ``size`` calls. Transport failures do not retry."""
    reason = _invalid_url_reason(url)
    if reason:
        return _batch_miss(size, reason, "invalid_url")
    if size < 1:
        return _batch_miss(size, "batch size must be at least 1", "malformed")
    extra = dict(headers or ())
    owns = client is None
    http = client or make_client(timeout=timeout)
    request: httpx.Request | None = None
    response: httpx.Response | None = None
    try:
        if budget is not None:
            try:
                budget.consume()
            except BudgetExceeded as exc:
                return _batch_miss(size, str(exc), "budget")
        started = time.monotonic()
        raw: bytes | None = None
        try:
            request = http.build_request(
                "POST",
                url,
                json=[
                    {
                        "jsonrpc": "2.0",
                        "id": i,
                        "method": method,
                        "params": params or [],
                    }
                    for i in range(1, size + 1)
                ],
                headers=extra or None,
            )
            response = http.send(request, stream=True)
            raw = response.read()
        except httpx.InvalidURL as exc:
            return _batch_miss(
                size,
                str(exc),
                "invalid_url",
                **_transport_fields(request, response),
            )
        except httpx.TimeoutException as exc:
            return _batch_miss(
                size,
                str(exc) or "timeout",
                "timeout",
                latency_ms=(time.monotonic() - started) * 1000,
                reachable=False,
                **_transport_fields(request, response),
            )
        except httpx.RequestError as exc:
            return _batch_miss(
                size,
                str(exc) or "connection failed",
                "connection",
                latency_ms=(time.monotonic() - started) * 1000,
                reachable=False,
                **_transport_fields(request, response),
            )
        finally:
            if response is not None:
                response.close()
        assert raw is not None and response is not None
        latency_ms = (time.monotonic() - started) * 1000
        transport = _transport_fields(request, response)
        if response.status_code >= 400:
            code = response.status_code
            body_msg = _jsonrpc_error_from_bytes(raw)
            error = body_msg or f"HTTP {code}"
            return BatchProbeResult(
                size=size,
                supported=False,
                partial=False,
                ok=False,
                reachable=True,
                latency_ms=latency_ms,
                n_ok=0,
                n_fail=size,
                error=error,
                error_class=_http_error_class(code, error),
                attempts=1,
                **transport,
            )
        try:
            payload = json.loads(raw)
        except ValueError:
            return BatchProbeResult(
                size=size,
                supported=False,
                partial=False,
                ok=False,
                reachable=True,
                latency_ms=latency_ms,
                n_ok=0,
                n_fail=size,
                error="response is not JSON",
                error_class="malformed",
                attempts=1,
                **transport,
            )
        supported, partial, n_ok, n_fail, error, error_class = _parse_batch_payload(
            payload, size
        )
        return BatchProbeResult(
            size=size,
            supported=supported,
            partial=partial,
            ok=supported and n_ok == size and n_fail == 0,
            reachable=True,
            latency_ms=latency_ms,
            n_ok=n_ok,
            n_fail=n_fail,
            error=error,
            error_class=error_class,
            attempts=1,
            **transport,
        )
    finally:
        if owns:
            http.close()


def _batch_miss(
    size: int,
    error: str,
    error_class: str,
    *,
    latency_ms: float | None = None,
    reachable: bool = False,
    http_version: str | None = None,
    encoding: str | None = None,
    bytes_out: int | None = None,
    bytes_in: int | None = None,
) -> BatchProbeResult:
    return BatchProbeResult(
        size=size,
        supported=False,
        partial=False,
        ok=False,
        reachable=reachable,
        latency_ms=latency_ms,
        n_ok=0,
        n_fail=size,
        error=error,
        error_class=error_class,
        attempts=0 if error_class in {"invalid_url", "budget"} else 1,
        http_version=http_version,
        encoding=encoding,
        bytes_out=bytes_out,
        bytes_in=bytes_in,
    )


def _parse_batch_payload(
    payload: Any, size: int
) -> tuple[bool, bool, int, int, str | None, str | None]:
    """Classify a JSON-RPC batch body. A single object means batch is unsupported."""
    if isinstance(payload, dict):
        message = (
            _error_message(payload.get("error"))
            if payload.get("error")
            else "JSON-RPC batch response is not an array"
        )
        return False, False, 0, size, message, "batch_unsupported"
    if not isinstance(payload, list):
        return False, False, 0, size, "JSON-RPC response is not an array", "malformed"
    by_id: dict[Any, dict[str, Any]] = {}
    for item in payload:
        if isinstance(item, dict) and "id" in item:
            by_id[item.get("id")] = item
    n_ok = 0
    n_fail = 0
    first_error: str | None = None
    first_class: str | None = None
    for i in range(1, size + 1):
        item = by_id.get(i)
        if item is None:
            n_fail += 1
            if first_error is None:
                first_error = f"missing response id {i}"
                first_class = "partial"
            continue
        if item.get("error"):
            n_fail += 1
            if first_error is None:
                first_error = _error_message(item.get("error"))
                first_class = (
                    "rate_limit" if is_rate_limit_message(first_error) else "jsonrpc"
                )
            continue
        n_ok += 1
    extra = len(payload) != size
    missing = n_ok + n_fail < size or extra
    if n_fail == 0 and not extra:
        return True, False, n_ok, 0, None, None
    if missing and first_class is None:
        first_error = first_error or "batch response length does not match"
        first_class = "partial"
    return True, True, n_ok, n_fail, first_error, first_class or "partial"


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
