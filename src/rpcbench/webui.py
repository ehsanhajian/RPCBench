"""Live web UI during a compare. Default bind is 127.0.0.1; opt-in for VPS."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, TextIO
from urllib.parse import urlparse

from rpcbench.freshness import parse_block_height
from rpcbench.rpc import ProbeResult
from rpcbench.tui import ProviderLive, _mean

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765
_LATENCY_WINDOW = 120
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class WebHostError(ValueError):
    """Invalid --web-host."""


def normalize_web_host(host: str) -> str:
    """Return a bind address. Default callers use 127.0.0.1; VPS uses 0.0.0.0."""
    text = (host or "").strip()
    if not text:
        raise WebHostError("--web-host is empty")
    if text.lower() == "localhost":
        return "127.0.0.1"
    # Reject whitespace / control chars; allow IPv4, IPv6, hostnames, 0.0.0.0, ::
    if any(ch.isspace() for ch in text):
        raise WebHostError(f"invalid --web-host {host!r}")
    return text


def is_loopback_host(host: str) -> bool:
    return host in _LOOPBACK or host == "127.0.0.1"


class LiveWebUi:
    """Serve live charts and the final HTML report (default: 127.0.0.1)."""

    def __init__(
        self,
        names: list[str],
        *,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        enabled: bool = True,
        open_browser: bool = True,
        log: TextIO | None = None,
    ) -> None:
        self.enabled = enabled
        self.host = normalize_web_host(host)
        self.port = int(port)
        self.open_browser = open_browser
        self.log = log or sys.stderr
        self.providers = {name: ProviderLive(name=name) for name in names}
        self.order = list(names)
        self.heights: dict[str, int | None] = {name: None for name in names}
        self.latencies: dict[str, list[float]] = {name: [] for name in names}
        self.phase = "starting"
        self.aborted = False
        self.done = False
        self.report_html: str | None = None
        self.started_at = time.monotonic()
        self._lock = threading.Lock()
        self._abort = threading.Event()
        self._quit = threading.Event()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url: str | None = None

    def start(self) -> str | None:
        if not self.enabled:
            return None
        port = self.port
        if port == 0:
            port = _free_port(self.host)
        handler = _make_handler(self)
        httpd = ThreadingHTTPServer((self.host, port), handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        self.port = httpd.server_address[1]
        browse_host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        # Bracket IPv6 literals in URLs.
        if ":" in browse_host and not browse_host.startswith("["):
            url_host = f"[{browse_host}]"
        else:
            url_host = browse_host
        self.url = f"http://{url_host}:{self.port}/"
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="rpcbench-webui", daemon=True
        )
        self._thread.start()
        if is_loopback_host(self.host):
            print(f"rpcbench: web UI {self.url}", file=self.log)
        else:
            print(
                f"rpcbench: web UI bound {self.host}:{self.port} "
                f"(no auth — open http://<this-host>:{self.port}/)",
                file=self.log,
            )
        if self.open_browser and is_loopback_host(self.host):
            try:
                webbrowser.open(self.url)
            except Exception:
                pass
        return self.url

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        self._quit.set()
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def request_abort(self) -> None:
        self._abort.set()
        with self._lock:
            self.aborted = True
            self.phase = "stopping"

    def request_quit(self) -> None:
        self._quit.set()

    def should_abort(self) -> bool:
        return self._abort.is_set()

    def wait_until_quit(self, *, timeout: float | None = None) -> None:
        if not self.enabled:
            return
        self._quit.wait(timeout=timeout)

    def record(self, name: str, hit: ProbeResult, *, kind: str = "sample") -> None:
        with self._lock:
            row = self.providers.get(name)
            if row is None:
                row = ProviderLive(name=name)
                self.providers[name] = row
                self.order.append(name)
                self.heights[name] = None
                self.latencies[name] = []
            if kind != "warmup":
                row.record(hit)
                if hit.ok and hit.latency_ms is not None:
                    series = self.latencies.setdefault(name, [])
                    series.append(float(hit.latency_ms))
                    if len(series) > _LATENCY_WINDOW:
                        del series[: len(series) - _LATENCY_WINDOW]
                height = parse_block_height(hit.result) if hit.ok else None
                if height is not None:
                    self.heights[name] = height
            self.phase = kind

    def set_report(self, html: str) -> None:
        with self._lock:
            self.report_html = html

    def finish(self, *, aborted: bool = False) -> None:
        with self._lock:
            self.aborted = aborted or self.aborted
            self.done = True
            self.phase = "aborted" if self.aborted else "done"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            cohort = [h for h in self.heights.values() if h is not None]
            top = max(cohort) if cohort else None
            providers = []
            for name in self.order:
                row = self.providers[name]
                height = self.heights.get(name)
                lag = None if top is None or height is None else top - height
                providers.append(
                    {
                        "name": name,
                        "n": row.n,
                        "n_ok": row.n_ok,
                        "n_fail": row.n_fail,
                        "p50": row.p50(),
                        "p95": row.p95(),
                        "error_rate": row.error_rate(),
                        "rps": row.rps(),
                        "height": height,
                        "lag_blocks": lag,
                        "latencies": list(self.latencies.get(name, [])),
                    }
                )
            elapsed = time.monotonic() - self.started_at
            return {
                "phase": self.phase,
                "aborted": self.aborted,
                "done": self.done,
                "has_report": self.report_html is not None,
                "elapsed_s": round(elapsed, 2),
                "providers": providers,
                "totals": {
                    "n": sum(p["n"] for p in providers),
                    "n_ok": sum(p["n_ok"] for p in providers),
                    "n_fail": sum(p["n_fail"] for p in providers),
                    "rps": _cohort_rps(providers),
                    "error_rate": _cohort_error(providers),
                },
            }


def wants_web(*, web: bool, ci: bool = False) -> bool:
    """`--web` opts in; CI may still use it, but callers skip the linger."""
    del ci
    return bool(web)


def should_wait_web(*, ci: bool, stdin: TextIO | None = None) -> bool:
    if ci:
        return False
    stream = stdin or sys.stdin
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _free_port(host: str) -> int:
    family = socket.AF_INET6 if ":" in host and host.count(":") > 1 else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _cohort_rps(providers: list[dict[str, Any]]) -> float | None:
    means = []
    for row in providers:
        series = row.get("latencies") or []
        mean = _mean(series)
        if mean is not None and mean > 0:
            means.append(mean)
    if not means:
        return None
    # Rough aggregate: mean of per-provider implied RPS.
    return sum(1000.0 / m for m in means)


def _cohort_error(providers: list[dict[str, Any]]) -> float | None:
    n = sum(int(p["n"]) for p in providers)
    if n == 0:
        return None
    fails = sum(int(p["n_fail"]) for p in providers)
    return fails / n


def _make_handler(ui: LiveWebUi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._send(200, "text/html; charset=utf-8", _DASHBOARD_HTML.encode("utf-8"))
                return
            if path == "/api/live":
                body = json.dumps(ui.snapshot()).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
                return
            if path == "/report":
                html = ui.report_html
                if html is None:
                    body = (
                        "<!doctype html><meta charset=utf-8>"
                        "<title>rpcbench</title>"
                        "<p>Report not ready yet. The live view is at "
                        "<a href=/>/</a>.</p>"
                    ).encode("utf-8")
                    self._send(200, "text/html; charset=utf-8", body)
                    return
                self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
                return
            self._send(404, "text/plain; charset=utf-8", b"not found\n")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            if path == "/api/stop":
                ui.request_abort()
                if ui.done:
                    ui.request_quit()
                body = json.dumps({"ok": True, "stopping": True}).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
                return
            if path == "/api/quit":
                ui.request_quit()
                body = json.dumps({"ok": True}).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
                return
            self._send(404, "text/plain; charset=utf-8", b"not found\n")

        def _send(self, code: int, content_type: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>rpcbench live</title>
<style>
  :root {
    --bg: #0f1419;
    --panel: #1a222c;
    --ink: #e7eef7;
    --muted: #8b9bb0;
    --line: #2a3544;
    --ok: #3ecf8e;
    --bad: #ff6b6b;
    --accent: #5b9fd4;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--ink);
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 1rem 1.25rem;
    border-bottom: 1px solid var(--line);
  }
  h1 { margin: 0; font-size: 1.1rem; letter-spacing: 0.02em; }
  .meta { color: var(--muted); font-size: 0.9rem; }
  .actions { display: flex; gap: 0.5rem; }
  button, a.btn {
    appearance: none;
    border: 1px solid var(--line);
    background: var(--panel);
    color: var(--ink);
    padding: 0.45rem 0.85rem;
    border-radius: 6px;
    cursor: pointer;
    text-decoration: none;
    font: inherit;
  }
  button.stop { border-color: #7a3030; background: #3a1c1c; color: #ffb4b4; }
  button:disabled { opacity: 0.45; cursor: default; }
  main { padding: 1rem 1.25rem 2rem; display: grid; gap: 1rem; }
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 0.75rem;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.85rem 1rem;
  }
  .card .label { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; }
  .card .value { font-size: 1.35rem; margin-top: 0.25rem; font-variant-numeric: tabular-nums; }
  .panel {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.85rem 1rem;
  }
  canvas { width: 100%; height: 180px; display: block; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th, td { text-align: left; padding: 0.45rem 0.35rem; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
  td.num, th.num { text-align: right; }
  .ok { color: var(--ok); }
  .bad { color: var(--bad); }
  iframe {
    width: 100%;
    min-height: 70vh;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: #fff;
  }
  .hidden { display: none; }
</style>
</head>
<body>
<header>
  <div>
    <h1>rpcbench live</h1>
    <div class="meta" id="status">starting…</div>
  </div>
  <div class="actions">
    <a class="btn hidden" id="reportLink" href="/report" target="_blank">Open report</a>
    <button class="stop" id="stopBtn" type="button">Stop</button>
    <button class="hidden" id="quitBtn" type="button">Quit</button>
  </div>
</header>
<main>
  <section class="cards" id="cards"></section>
  <section class="panel">
    <div class="label" style="color:var(--muted);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:0.5rem">Latency (ms)</div>
    <canvas id="latencyChart" width="900" height="180"></canvas>
  </section>
  <section class="panel">
    <table>
      <thead>
        <tr>
          <th>provider</th>
          <th class="num">n</th>
          <th class="num">p50</th>
          <th class="num">p95</th>
          <th class="num">err</th>
          <th class="num">rps</th>
          <th class="num">lag</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </section>
  <section class="panel hidden" id="reportPanel">
    <div class="meta" style="margin-bottom:0.5rem">Final HTML report</div>
    <iframe id="reportFrame" title="rpcbench report"></iframe>
  </section>
</main>
<script>
const colors = ["#5b9fd4","#3ecf8e","#f0c14b","#c792ea","#ff6b6b","#82aaff","#7fdbca"];
function fmtMs(v){ if(v==null||!Number.isFinite(v)) return "—"; return v>=100?v.toFixed(0):v.toFixed(1); }
function fmtErr(v){ if(v==null||!Number.isFinite(v)) return "—"; return (100*v).toFixed(0)+"%"; }
function fmtRps(v){ if(v==null||!Number.isFinite(v)) return "—"; return v>=100?v.toFixed(0):v.toFixed(1); }
function fmtLag(v){ if(v==null) return "—"; return String(v); }

async function stopRun(){
  await fetch("/api/stop", {method:"POST"});
  document.getElementById("stopBtn").disabled = true;
  document.getElementById("status").textContent = "stopping… flushing reports";
}
async function quitUi(){
  await fetch("/api/quit", {method:"POST"});
  document.getElementById("status").textContent = "quit requested — CLI will exit";
}

document.getElementById("stopBtn").onclick = stopRun;
document.getElementById("quitBtn").onclick = quitUi;

function drawChart(providers){
  const canvas = document.getElementById("latencyChart");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 900;
  const cssH = 180;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,cssW,cssH);
  ctx.strokeStyle = "#2a3544";
  ctx.beginPath();
  ctx.moveTo(0, cssH-0.5); ctx.lineTo(cssW, cssH-0.5);
  ctx.stroke();
  let max = 1;
  providers.forEach(p => (p.latencies||[]).forEach(v => { if(v>max) max=v; }));
  providers.forEach((p, i) => {
    const pts = p.latencies || [];
    if(pts.length < 2) return;
    ctx.strokeStyle = colors[i % colors.length];
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    pts.forEach((v, idx) => {
      const x = (idx / (pts.length - 1)) * (cssW - 8) + 4;
      const y = cssH - 8 - (v / max) * (cssH - 16);
      if(idx===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
    });
    ctx.stroke();
  });
}

function render(data){
  const status = document.getElementById("status");
  let line = data.phase + " · " + data.elapsed_s + "s";
  if(data.aborted) line += " · aborted";
  if(data.done) line += " · done";
  status.textContent = line;

  const t = data.totals || {};
  const cards = [
    ["requests", t.n ?? 0],
    ["ok", t.n_ok ?? 0],
    ["errors", t.n_fail ?? 0],
    ["error rate", fmtErr(t.error_rate)],
    ["rps", fmtRps(t.rps)],
  ];
  document.getElementById("cards").innerHTML = cards.map(([k,v]) =>
    `<div class="card"><div class="label">${k}</div><div class="value">${v}</div></div>`
  ).join("");

  const rows = (data.providers||[]).map((p,i) => {
    const errCls = (p.error_rate||0) > 0 ? "bad" : "ok";
    return `<tr>
      <td><span style="color:${colors[i%colors.length]}">●</span> ${p.name}</td>
      <td class="num">${p.n}</td>
      <td class="num">${fmtMs(p.p50)}</td>
      <td class="num">${fmtMs(p.p95)}</td>
      <td class="num ${errCls}">${fmtErr(p.error_rate)}</td>
      <td class="num">${fmtRps(p.rps)}</td>
      <td class="num">${fmtLag(p.lag_blocks)}</td>
    </tr>`;
  }).join("");
  document.getElementById("rows").innerHTML = rows || `<tr><td colspan="7" class="meta">waiting for samples…</td></tr>`;
  drawChart(data.providers || []);

  if(data.has_report){
    document.getElementById("reportLink").classList.remove("hidden");
    document.getElementById("reportPanel").classList.remove("hidden");
    const frame = document.getElementById("reportFrame");
    if(frame.dataset.loaded !== "1"){
      frame.src = "/report";
      frame.dataset.loaded = "1";
    }
  }
  if(data.done){
    document.getElementById("stopBtn").classList.add("hidden");
    document.getElementById("quitBtn").classList.remove("hidden");
  }
}

async function tick(){
  try {
    const res = await fetch("/api/live");
    const data = await res.json();
    render(data);
  } catch (e) {}
}
tick();
setInterval(tick, 500);
</script>
</body>
</html>
"""
