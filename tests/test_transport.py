from __future__ import annotations

import gzip
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from rpcbench.cli import build_parser, main
from rpcbench.config import Endpoint, parse_endpoints
from rpcbench.html import format_html
from rpcbench.report import format_json, format_run, run_to_dict
from rpcbench.rpc import ProbeResult, make_client, probe
from rpcbench.run import EndpointOutcome, RunResult, run_endpoints, summarize, summarize_transport


RPC_OK = b'{"jsonrpc":"2.0","id":1,"result":"0x1"}'
RPC_LOGS = b'{"jsonrpc":"2.0","id":1,"result":[' + b'"0x' + b"ab" * 40 + b'",' * 80 + b'"0x00"]}'


class _PlainHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(RPC_OK)))
        self.end_headers()
        self.wfile.write(RPC_OK)

    def log_message(self, *_args: object) -> None:
        return


class _GzipHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = gzip.compress(RPC_OK)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def _ok(
    ms: float,
    *,
    method: str = "eth_blockNumber",
    size: int = 82,
) -> ProbeResult:
    return ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=ms,
        result="0x1",
        error=None,
        error_class=None,
        attempts=1,
        method=method,
        http_version="1.1",
        encoding="gzip",
        bytes_out=64,
        bytes_in=size,
    )


def _outcome(*hits: ProbeResult) -> EndpointOutcome:
    samples = tuple(hits)
    return EndpointOutcome(
        endpoint=Endpoint(name="local", url="http://127.0.0.1/local"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        transport=summarize_transport(samples),
    )


def test_probe_records_http11_and_byte_counts() -> None:
    server, url = _serve(_PlainHandler)
    try:
        hit = probe(url, "eth_blockNumber", retries=0)
    finally:
        server.shutdown()
        server.server_close()
    assert hit.ok
    assert hit.http_version == "1.1"
    assert hit.encoding is None
    assert hit.bytes_out is not None and hit.bytes_out > 0
    assert hit.bytes_in == len(RPC_OK)


def test_probe_records_gzip_wire_bytes() -> None:
    compressed = gzip.compress(RPC_OK)
    server, url = _serve(_GzipHandler)
    try:
        hit = probe(url, "eth_blockNumber", retries=0)
    finally:
        server.shutdown()
        server.server_close()
    assert hit.ok
    assert hit.http_version == "1.1"
    assert hit.encoding == "gzip"
    assert hit.bytes_in == len(compressed)


def test_run_summarizes_transport() -> None:
    server, url = _serve(_PlainHandler)
    cfg = parse_endpoints({"endpoints": [{"name": "local", "url": url}]})
    try:
        result = run_endpoints(cfg, samples=2, warmup=0, budget=16)
    finally:
        server.shutdown()
        server.server_close()
    assert result.http == "1.1"
    summary = result.outcomes[0].transport
    assert summary is not None
    assert summary.http_version == "1.1"
    assert summary.bytes_in_p95 == len(RPC_OK)
    compact = format_run(result, color=False)
    assert "Transport" not in compact
    full = format_run(result, verbose=True, color=False)
    assert "Transport  (negotiated proto" in full
    assert "1.1" in full.split("Transport", 1)[1]
    assert "finding" not in full.lower()
    data = json.loads(format_json(result))
    assert data["http"] == "1.1"
    sample = data["providers"][0]["samples"][0]
    assert sample["http_version"] == "1.1"
    assert sample["bytes_in"] == len(RPC_OK)
    assert data["providers"][0]["transport"]["bytes_in_p95"] == len(RPC_OK)


def test_html_size_vs_latency_highlights_logs() -> None:
    result = RunResult(
        method="mix",
        params=(),
        samples=2,
        warmup=0,
        timeout=5.0,
        budget=16,
        outcomes=(
            _outcome(
                _ok(12.0, method="eth_blockNumber", size=80),
                _ok(40.0, method="eth_getLogs", size=len(RPC_LOGS)),
            ),
        ),
        budget_remaining=8,
        profile="mix",
    )
    html = format_html(result)
    assert "Transport" in html
    assert 'aria-label="size vs latency"' in html
    assert "eth_getLogs" in html
    assert "response bytes (log)" in html
    assert "fat getLogs" not in html
    assert ">getLogs</text>" in html or ">getLogs<" in html
    assert "blockNumber" in html
    def cx_for(method: str) -> float:
        match = re.search(
            rf'cx="([0-9.]+)"[^>]*>\s*<title>[^<]*{method}',
            html,
        )
        assert match is not None, method
        return float(match.group(1))

    assert cx_for("eth_getLogs") > cx_for("eth_blockNumber") + 80
    assert "finding" not in html.lower()
    fold = html.split("<!-- fold -->", 1)[0]
    assert "size vs latency" not in fold


def test_cli_http_flags() -> None:
    ns = build_parser().parse_args(["run", "--endpoints", "x.yaml", "--http2"])
    assert ns.http2 is True
    assert ns.http1 is False
    ns = build_parser().parse_args(["run", "--endpoints", "x.yaml", "--http1"])
    assert ns.http1 is True
    assert ns.http2 is False


def test_cli_http1_and_http2_rejected(tmp_path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(["run", "--endpoints", str(cfg), "--http1", "--http2"])
    assert code == 2
    assert "pick --http1 or --http2" in capsys.readouterr().err


def test_cli_http2_is_passed(tmp_path, monkeypatch) -> None:
    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        seen.update(kwargs)
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--http2",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    assert seen["http2"] is True


def test_make_client_http2_enables_h2() -> None:
    http = make_client(timeout=1.0, http2=True)
    try:
        assert http._transport._pool._http2 is True  # noqa: SLF001
    finally:
        http.close()


def test_json_dict_includes_transport_nulls() -> None:
    data = run_to_dict(
        RunResult(
            method="eth_blockNumber",
            params=(),
            samples=1,
            warmup=0,
            timeout=5.0,
            budget=8,
            outcomes=(_outcome(_ok(10.0)),),
            budget_remaining=4,
        )
    )
    assert data["http"] == "1.1"
    transport = data["ranking"][0]["transport"]
    assert transport["http_version"] == "1.1"
    assert transport["encoding"] == "gzip"
    assert transport["bytes_in_p95"] == 82
    assert "finding" not in json.dumps(data).lower()
