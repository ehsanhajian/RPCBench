from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from rpcbench.config import parse_endpoints
from rpcbench.report import format_json, format_run
from rpcbench.rpc import ProbeResult, make_client, probe
from rpcbench.run import run_endpoints
from rpcbench.timing import HttpTiming, TimingScratch, TimingStream, snapshot_timing


RPC_OK = b'{"jsonrpc":"2.0","id":1,"result":"0x1"}'


class _CountingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        time.sleep(0.02)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(RPC_OK)))
        self.end_headers()
        self.wfile.write(RPC_OK)

    def log_message(self, *_args: object) -> None:
        return


class _CountingServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, _CountingHandler)
        self.connections = 0
        self._lock = threading.Lock()

    def get_request(self):  # type: ignore[override]
        sock, addr = super().get_request()
        with self._lock:
            self.connections += 1
        return sock, addr


def _serve() -> tuple[_CountingServer, str]:
    server = _CountingServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def test_probe_splits_handshake_server_and_payload() -> None:
    server, url = _serve()
    try:
        hit = probe(url, "eth_blockNumber", retries=0)
    finally:
        server.shutdown()
        server.server_close()
    assert hit.ok
    assert hit.timing is not None
    assert hit.timing.dns_ms is not None
    assert hit.timing.tcp_ms is not None
    assert hit.timing.tls_ms is None
    assert hit.timing.handshake_ms() > 0
    assert hit.timing.server_ms is not None and hit.timing.server_ms >= 15
    assert hit.timing.body_ms is not None
    assert hit.timing.parse_ms is not None
    assert hit.timing.payload_ms() is not None
    assert hit.latency_ms is not None
    parts = hit.timing.handshake_ms() + hit.timing.server_ms + (hit.timing.body_ms or 0)
    assert parts == pytest.approx(hit.latency_ms, abs=8.0)


def test_new_connection_opens_more_sockets_than_keepalive() -> None:
    server, url = _serve()
    cfg = parse_endpoints({"endpoints": [{"name": "local", "url": url}]})
    try:
        warm = run_endpoints(
            cfg, samples=3, warmup=0, budget=32, new_connection=False
        )
        keep_n = server.connections
        server.shutdown()
        server.server_close()
        server2, url2 = _serve()
        cfg2 = parse_endpoints({"endpoints": [{"name": "local", "url": url2}]})
        try:
            cold = run_endpoints(
                cfg2, samples=3, warmup=0, budget=32, new_connection=True
            )
            new_n = server2.connections
        finally:
            server2.shutdown()
            server2.server_close()
    finally:
        pass
    assert warm.connection == "keepalive"
    assert cold.connection == "new"
    assert keep_n == 1
    assert new_n > keep_n
    text = format_run(warm, color=False)
    assert "Timing" not in text
    assert "conn=keepalive" in text
    full = format_run(warm, verbose=True, color=False)
    assert "Timing" in full
    assert "handshake" in full
    payload = json.loads(format_json(warm))
    assert payload["connection"] == "keepalive"
    timing = payload["comparison"][0]["timing"]
    assert timing["handshake"]["p95_ms"] is not None
    assert timing["server"]["p95_ms"] is not None
    assert timing["payload"]["p95_ms"] is not None
    assert payload["providers"][0]["samples"][0]["timing"]["handshake_ms"] is not None


def test_tls_phase_is_recorded_on_start_tls() -> None:
    class Dummy:
        def start_tls(self, *args, **kwargs):
            time.sleep(0.02)
            return self

        def read(self, *args, **kwargs):
            return b""

        def write(self, *args, **kwargs):
            return None

        def close(self) -> None:
            return None

        def get_extra_info(self, info: str):
            return None

    from rpcbench.timing import begin_timing, current_scratch, end_timing

    begin_timing()
    TimingStream(Dummy()).start_tls(ssl_context=None)
    scratch = current_scratch()
    assert scratch is not None
    assert scratch.tls_ms is not None
    assert scratch.tls_ms >= 15
    end_timing()


def test_snapshot_handshake_zero_when_connection_reused() -> None:
    scratch = TimingScratch()
    started = time.monotonic()
    headers_at = started + 0.01
    body_at = headers_at + 0.002
    row = snapshot_timing(scratch, started, headers_at, body_at, 0.5)
    assert row.handshake_ms() == 0.0
    assert row.server_ms == pytest.approx(10.0, abs=0.5)
    assert row.body_ms == pytest.approx(2.0, abs=0.5)
    assert row.parse_ms == 0.5


def test_report_timing_table_uses_p95_not_ranking() -> None:
    from rpcbench.config import Endpoint
    from rpcbench.run import EndpointOutcome, RunResult, summarize, summarize_timing

    fast = ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=80.0,
        result="0x1",
        error=None,
        error_class=None,
        attempts=1,
        timing=HttpTiming(
            dns_ms=5.0,
            tcp_ms=10.0,
            tls_ms=20.0,
            server_ms=40.0,
            body_ms=4.0,
            parse_ms=1.0,
        ),
    )
    slow_net = ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=90.0,
        result="0x1",
        error=None,
        error_class=None,
        attempts=1,
        timing=HttpTiming(
            dns_ms=1.0,
            tcp_ms=2.0,
            tls_ms=3.0,
            server_ms=80.0,
            body_ms=3.0,
            parse_ms=1.0,
        ),
    )
    samples = (fast, slow_net)
    outcome = EndpointOutcome(
        endpoint=Endpoint(name="node", url="http://127.0.0.1/node"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        timing=summarize_timing(samples),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=2,
        warmup=0,
        timeout=10.0,
        budget=32,
        outcomes=(outcome,),
        budget_remaining=20,
        connection="new",
    )
    text = format_run(result, color=False)
    assert "Timing  (handshake = DNS+TCP+TLS" not in text
    assert "conn=new" in text
    full = format_run(result, verbose=True, color=False)
    assert "Timing  (handshake = DNS+TCP+TLS" in full
    assert "  35.0ms" in full.split("Timing", 1)[1] or "35.0ms" in full
    assert "finding" not in text.lower()
    data = json.loads(format_json(result))
    assert data["connection"] == "new"
    assert data["comparison"][0]["timing"]["handshake"]["p95_ms"] == pytest.approx(35.0)
    assert data["comparison"][0]["timing"]["server"]["p95_ms"] == pytest.approx(80.0)
    verbose = format_run(result, verbose=True, color=False)
    assert "hs=" in verbose
    assert "srv=" in verbose
    assert "pay=" in verbose


def test_make_client_new_connection_disables_keepalive() -> None:
    http = make_client(timeout=1.0, new_connection=True)
    try:
        limits = http._transport._pool._max_keepalive_connections  # noqa: SLF001
        assert limits == 0
    finally:
        http.close()


def test_mock_transport_still_records_server_and_parse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hit = probe("http://127.0.0.1:8545", "eth_blockNumber", client=client, retries=0)
    assert hit.ok
    assert hit.timing is not None
    assert hit.timing.handshake_ms() == 0.0
    assert hit.timing.server_ms is not None
    assert hit.timing.parse_ms is not None
