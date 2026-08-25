"""HTTP phase timings. TLS here is handshake latency, not a certificate check."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpcore
import httpx
from httpcore._backends.sync import SyncBackend, SyncStream
from httpcore._exceptions import ConnectError, ConnectTimeout, map_exceptions

CONN_KEEPALIVE = "keepalive"
CONN_NEW = "new"

_tls = threading.local()


@dataclass
class TimingScratch:
    dns_ms: float | None = None
    tcp_ms: float | None = None
    tls_ms: float | None = None


@dataclass(frozen=True)
class HttpTiming:
    dns_ms: float | None = None
    tcp_ms: float | None = None
    tls_ms: float | None = None
    server_ms: float | None = None
    body_ms: float | None = None
    parse_ms: float | None = None

    def handshake_ms(self) -> float:
        if self.dns_ms is None and self.tcp_ms is None and self.tls_ms is None:
            return 0.0
        return (self.dns_ms or 0.0) + (self.tcp_ms or 0.0) + (self.tls_ms or 0.0)

    def payload_ms(self) -> float | None:
        if self.body_ms is None and self.parse_ms is None:
            return None
        return (self.body_ms or 0.0) + (self.parse_ms or 0.0)


def begin_timing() -> TimingScratch:
    scratch = TimingScratch()
    _tls.scratch = scratch
    return scratch


def current_scratch() -> TimingScratch | None:
    return getattr(_tls, "scratch", None)


def end_timing() -> None:
    _tls.scratch = None


def snapshot_timing(
    scratch: TimingScratch,
    started: float,
    headers_at: float | None,
    body_at: float | None,
    parse_ms: float | None,
) -> HttpTiming:
    handshake = HttpTiming(
        dns_ms=scratch.dns_ms,
        tcp_ms=scratch.tcp_ms,
        tls_ms=scratch.tls_ms,
    ).handshake_ms()
    server_ms = None
    body_ms = None
    if headers_at is not None:
        ttfb = (headers_at - started) * 1000
        server_ms = max(0.0, ttfb - handshake)
    if headers_at is not None and body_at is not None:
        body_ms = max(0.0, (body_at - headers_at) * 1000)
    return HttpTiming(
        dns_ms=scratch.dns_ms,
        tcp_ms=scratch.tcp_ms,
        tls_ms=scratch.tls_ms,
        server_ms=server_ms,
        body_ms=body_ms,
        parse_ms=parse_ms,
    )


class TimingStream(httpcore.NetworkStream):
    def __init__(self, inner: httpcore.NetworkStream) -> None:
        self._inner = inner

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, timeout)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, timeout)

    def close(self) -> None:
        self._inner.close()

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)

    def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        t0 = time.monotonic()
        upgraded = self._inner.start_tls(ssl_context, server_hostname, timeout)
        scratch = current_scratch()
        if scratch is not None:
            scratch.tls_ms = (time.monotonic() - t0) * 1000
        return TimingStream(upgraded)


class TimingBackend(SyncBackend):
    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        if socket_options is None:
            socket_options = []
        scratch = current_scratch()
        t0 = time.monotonic()
        exc_map = {socket.timeout: ConnectTimeout, OSError: ConnectError}
        with map_exceptions(exc_map):
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if scratch is not None:
            scratch.dns_ms = (time.monotonic() - t0) * 1000
        source_address = None if local_address is None else (local_address, 0)
        last_err: OSError | None = None
        t1 = time.monotonic()
        with map_exceptions(exc_map):
            for family, socktype, proto, _canon, sockaddr in infos:
                sock = socket.socket(family, socktype, proto)
                try:
                    sock.settimeout(timeout)
                    if source_address is not None:
                        sock.bind(source_address)
                    for option in socket_options:
                        sock.setsockopt(*option)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.connect(sockaddr)
                    if scratch is not None:
                        scratch.tcp_ms = (time.monotonic() - t1) * 1000
                    return TimingStream(SyncStream(sock))
                except OSError as exc:
                    last_err = exc
                    sock.close()
            if last_err is not None:
                raise last_err
            raise ConnectError("failed to connect")


class TimingTransport(httpx.HTTPTransport):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pool._network_backend = TimingBackend()
