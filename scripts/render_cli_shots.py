"""Render compact vs --verbose CLI shots for the README."""

from __future__ import annotations

import html
import re
from pathlib import Path

from rpcbench.config import Endpoint
from rpcbench.consistency import Consistency
from rpcbench.freshness import Freshness
from rpcbench.report import format_run
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, summarize, summarize_timing
from rpcbench.tags import TagSnapshot
from rpcbench.timing import HttpTiming

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
_ANSI = re.compile(r"\033\[([0-9;]*)m")
_FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, Liberation Mono, monospace"
_FG = "#e6edf3"
_BG = "#0d1117"
_BAR = "#161b22"
_DIM = "#8b949e"
_GREEN = "#3fb950"
_RED = "#f85149"
CHAR_W = 8.05
LINE_H = 18
PAD_X = 18
PAD_Y = 14
BAR_H = 36


def _ok(ms: float, *, server: float | None = None) -> ProbeResult:
    wait = server if server is not None else max(ms - 1.0, 0.5)
    return ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=ms,
        result="0x1",
        error=None,
        error_class=None,
        attempts=1,
        timing=HttpTiming(
            server_ms=wait,
            body_ms=0.8,
            parse_ms=0.2,
        ),
    )


def _fail(cls: str, error: str) -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=False,
        latency_ms=None,
        result=None,
        error=error,
        error_class=cls,
        attempts=1,
    )


def _fresh(height: int, lag: int, verdict: str) -> Freshness:
    return Freshness(
        height=height,
        height_hex=hex(height),
        lag_blocks=lag,
        lag_s=lag * 12.0,
        verdict=verdict,
        cohort_height=100,
    )


def _agree(digest: str, verdict: str = "agree") -> Consistency:
    canon = "0x" + "aa" * 32
    return Consistency(
        hash=digest,
        number=100,
        verdict=verdict,
        pin_height=100,
        canonical_hash=canon if verdict != "unknown" else None,
    )


def _tags(height: int, *, skip: str | None = None) -> tuple[TagSnapshot, ...]:
    rows = []
    for name in ("latest", "safe", "finalized"):
        if skip:
            rows.append(
                TagSnapshot(
                    tag=name,
                    latency_ms=None,
                    height=None,
                    hash=None,
                    freshness=None,
                    skipped=True,
                    skip_reason=skip,
                )
            )
            continue
        rows.append(
            TagSnapshot(
                tag=name,
                latency_ms=18.0 if name == "latest" else 22.0,
                height=height,
                hash="0x" + "aa" * 32,
                freshness=_fresh(height, 0, "fresh"),
                skipped=False,
                skip_reason=None,
            )
        )
    return tuple(rows)


def _outcome(
    name: str,
    url: str,
    samples: tuple[ProbeResult, ...],
    *,
    freshness: Freshness | None = None,
    consistency: Consistency | None = None,
    client: str | None = None,
    tags: tuple[TagSnapshot, ...] = (),
    burst_n: int = 0,
) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name=name, url=url),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        freshness=freshness,
        consistency=consistency,
        client=client,
        tags=tags,
        burst_stats=summarize(samples[:burst_n]) if burst_n else None,
        steady_stats=summarize(samples[burst_n:]) if burst_n else None,
        timing=summarize_timing(samples),
    )


def demo_result() -> RunResult:
    digest = "0x" + "aa" * 32
    public = _outcome(
        "publicnode",
        "https://ethereum.publicnode.com",
        (_ok(81.0, server=80.0), _ok(84.0, server=83.0), _ok(86.0, server=85.0)),
        freshness=_fresh(100, 0, "fresh"),
        consistency=_agree(digest),
        client="Geth/v1.14.12-stable",
        tags=_tags(100),
        burst_n=2,
    )
    drpc = _outcome(
        "drpc",
        "https://eth.drpc.org",
        (_ok(88.0, server=87.0), _ok(91.0, server=90.0), _ok(94.0, server=93.0)),
        freshness=_fresh(100, 0, "fresh"),
        consistency=_agree(digest),
        client="erigon/2.60.0",
        tags=_tags(100),
        burst_n=2,
    )
    merkle = _outcome(
        "merkle",
        "https://eth.merkle.io",
        (
            _ok(42.0, server=41.0),
            _fail("rate_limit", "Too Many Requests"),
            _fail("rate_limit", "Too Many Requests"),
        ),
        freshness=_fresh(100, 0, "fresh"),
        consistency=_agree(digest),
        tags=_tags(100, skip="rate_limit"),
        burst_n=2,
    )
    local = _outcome(
        "local",
        "http://127.0.0.1:8545",
        (
            _fail("connection", "connection refused"),
            _fail("connection", "connection refused"),
            _fail("connection", "connection refused"),
        ),
        tags=_tags(100, skip="connection"),
        burst_n=2,
    )
    return RunResult(
        method="eth_blockNumber",
        params=(),
        samples=3,
        warmup=0,
        timeout=5.0,
        budget=128,
        outcomes=(public, drpc, merkle, local),
        budget_remaining=96,
        sequence_id="a1b2c3d4",
        sample_budget="short",
        burst=2,
        pin_height=100,
        cohort_height=100,
        canonical_hash=digest,
    )


def _spans(line: str) -> list[tuple[str, str, bool]]:
    color = _FG
    bold = False
    out: list[tuple[str, str, bool]] = []
    pos = 0
    for match in _ANSI.finditer(line):
        if match.start() > pos:
            out.append((line[pos : match.start()], color, bold))
        codes = [part for part in match.group(1).split(";") if part]
        if not codes or codes == ["0"]:
            color = _FG
            bold = False
        else:
            if "1" in codes:
                bold = True
            if "32" in codes:
                color = _GREEN
            if "31" in codes:
                color = _RED
        pos = match.end()
    if pos < len(line):
        out.append((line[pos:], color, bold))
    if not out:
        out.append(("", _FG, False))
    return out


def _visible(line: str) -> str:
    return _ANSI.sub("", line)


def render_svg(text: str, title: str) -> str:
    lines = text.splitlines() or [""]
    cols = max((len(_visible(ln)) for ln in lines), default=40)
    width = int(PAD_X * 2 + cols * CHAR_W)
    body_h = PAD_Y * 2 + len(lines) * LINE_H
    height = BAR_H + body_h
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{html.escape(title)}">',
        f'<rect width="{width}" height="{height}" rx="10" fill="{_BG}"/>',
        f'<rect width="{width}" height="{BAR_H}" rx="10" fill="{_BAR}"/>',
        f'<rect y="{BAR_H - 10}" width="{width}" height="10" fill="{_BAR}"/>',
        '<circle cx="18" cy="18" r="5" fill="#ff5f57"/>',
        '<circle cx="36" cy="18" r="5" fill="#febc2e"/>',
        '<circle cx="54" cy="18" r="5" fill="#28c840"/>',
        f'<text x="{width / 2}" y="23" text-anchor="middle" fill="{_DIM}" '
        f'font-family="{_FONT}" font-size="12">{html.escape(title)}</text>',
    ]
    y = BAR_H + PAD_Y + 13
    for line in lines:
        x = PAD_X
        for chunk, color, bold in _spans(line):
            if not chunk:
                continue
            weight = ' font-weight="700"' if bold else ""
            parts.append(
                f'<text x="{x:.1f}" y="{y}" fill="{color}" font-family="{_FONT}" '
                f'font-size="13"{weight} xml:space="preserve">'
                f"{html.escape(chunk)}</text>"
            )
            x += len(chunk) * CHAR_W
        y += LINE_H
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    result = demo_result()
    OUT.mkdir(parents=True, exist_ok=True)
    shots = {
        "cli-compact.svg": (
            format_run(result, verbose=False, color=True),
            "rpcbench compare --budget short",
        ),
        "cli-verbose.svg": (
            format_run(result, verbose=True, color=True),
            "rpcbench compare --budget short --verbose",
        ),
    }
    for name, (body, title) in shots.items():
        path = OUT / name
        path.write_text(render_svg(body, title), encoding="utf-8")
        print(f"{path.relative_to(ROOT)}  {body.count(chr(10))} lines")


if __name__ == "__main__":
    main()
