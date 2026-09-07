"""Render README CLI shots. Re-run when format_run output changes.

    PYTHONPATH=src python scripts/render_cli_shots.py
"""

from __future__ import annotations

import html
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from rpcbench.config import Endpoint
from rpcbench.consistency import Consistency
from rpcbench.freshness import Freshness
from rpcbench.html import _heatmap_grid, _sample_latencies
from rpcbench.report import format_run, run_to_dict
from rpcbench.rpc import ProbeResult
from rpcbench.run import (
    EndpointOutcome,
    RunResult,
    summarize,
    summarize_timing,
    summarize_transport,
)
from rpcbench.tags import TagSnapshot
from rpcbench.timing import HttpTiming

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
_ANSI = re.compile(r"\033\[([0-9;]*)m")
_FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, Liberation Mono, monospace"
_FG = "#e6edf3"
_BG = "#0d1117"
_BAR = "#161b22"
_PANEL = "#161b22"
_ACCENT = "#388bfd"
_DIM = "#8b949e"
_GREEN = "#3fb950"
_RED = "#f85149"
_AMBER = "#d29922"
CHAR_W = 8.05
LINE_H = 18
PAD_X = 18
PAD_Y = 14
BAR_H = 36
_SHOT_MARK = {
    "git_sha": "a1b2c3d4e5f6",
    "started_at": "2026-08-25T12:00:00Z",
    "vantage": "readme",
}


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
        http_version="1.1",
        encoding="gzip",
        bytes_out=64,
        bytes_in=82,
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
        transport=summarize_transport(samples),
    )


def mix_result() -> RunResult:
    from rpcbench.methods import MIX_PROFILE

    digest = "0x" + "aa" * 32

    def stacked(base: float) -> tuple[ProbeResult, ...]:
        hits: list[ProbeResult] = []
        rows: list[tuple[str, object]] = []
        for i, spec in enumerate(MIX_PROFILE):
            chunk = (_ok(base + i * 6), _ok(base + i * 6 + 3))
            hits.extend(chunk)
            rows.append((spec.name, summarize(chunk)))
        return tuple(hits), tuple(rows)

    public_hits, public_methods = stacked(70.0)
    drpc_hits, drpc_methods = stacked(78.0)
    public = replace(
        _outcome(
            "publicnode",
            "https://ethereum.publicnode.com",
            public_hits,
            freshness=_fresh(100, 0, "fresh"),
            consistency=_agree(digest),
        ),
        by_method=public_methods,
    )
    drpc = replace(
        _outcome(
            "drpc",
            "https://eth.drpc.org",
            drpc_hits,
            freshness=_fresh(100, 0, "fresh"),
            consistency=_agree(digest),
        ),
        by_method=drpc_methods,
    )
    return RunResult(
        method="eth_blockNumber",
        params=(),
        samples=2,
        warmup=0,
        timeout=5.0,
        budget=128,
        outcomes=(public, drpc),
        budget_remaining=80,
        sequence_id="m1x00001",
        sample_budget="short",
        profile="mix",
        workload=MIX_PROFILE,
        pin_height=100,
        cohort_height=100,
        canonical_hash=digest,
        **_SHOT_MARK,
    )


def cold_result() -> RunResult:
    digest = "0x" + "aa" * 32

    def cold(ms: float, tls: float) -> ProbeResult:
        return ProbeResult(
            ok=True,
            reachable=True,
            latency_ms=ms,
            result="0x1",
            error=None,
            error_class=None,
            attempts=1,
            timing=HttpTiming(
                dns_ms=9.0,
                tcp_ms=5.0,
                tls_ms=tls,
                server_ms=max(ms - tls - 15.0, 40.0),
                body_ms=1.2,
                parse_ms=0.2,
            ),
            http_version="1.1",
            encoding="gzip",
            bytes_out=64,
            bytes_in=82,
        )

    public = _outcome(
        "publicnode",
        "https://ethereum.publicnode.com",
        (cold(140.0, 48.0), cold(144.0, 50.0), cold(150.0, 52.0)),
        freshness=_fresh(100, 0, "fresh"),
        consistency=_agree(digest),
    )
    drpc = _outcome(
        "drpc",
        "https://eth.drpc.org",
        (cold(160.0, 58.0), cold(168.0, 60.0), cold(172.0, 61.0)),
        freshness=_fresh(100, 0, "fresh"),
        consistency=_agree(digest),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=3,
        warmup=0,
        timeout=5.0,
        budget=128,
        outcomes=(public, drpc),
        budget_remaining=110,
        sequence_id="c01d0001",
        sample_budget="short",
        pin_height=100,
        cohort_height=100,
        canonical_hash=digest,
        connection="new",
        **_SHOT_MARK,
    )
    return result


def _extract(text: str, start: str, stop: str | None = None) -> str:
    block = text.split(start, 1)[1]
    if stop is not None and stop in block:
        block = block.split(stop, 1)[0]
    return start + block.rstrip() + "\n"


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
        **_SHOT_MARK,
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


def _clip(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def render_html_shot(result: RunResult, title: str, *, max_signals: int = 2) -> str:
    """Browser-chrome SVG of the HTML fold (ranking, heatmap, signals)."""
    data = run_to_dict(result)
    ranking = data["ranking"]
    providers = {row["name"]: row for row in data["providers"]}
    step_names, heat_rows = _heatmap_grid(data)
    cards: list[tuple[str, dict[str, Any]]] = []
    for row in ranking:
        for sig in (row.get("verdict") or {}).get("signals") or []:
            cards.append((row["name"], sig))
            if len(cards) >= max_signals:
                break
        if len(cards) >= max_signals:
            break

    width = 800
    left = 16
    inner = width - 32
    row_h = 20
    y = BAR_H + 14
    parts: list[str] = []

    def txt(
        x: float,
        yy: float,
        text: str,
        *,
        fill: str = _FG,
        size: int = 12,
        weight: str = "",
        anchor: str = "start",
    ) -> None:
        extra = f' font-weight="{weight}"' if weight else ""
        extra += f' text-anchor="{anchor}"' if anchor != "start" else ""
        parts.append(
            f'<text x="{x:.1f}" y="{yy:.1f}" fill="{fill}" font-family="{_FONT}" '
            f'font-size="{size}"{extra} xml:space="preserve">'
            f"{html.escape(text)}</text>"
        )

    def panel(height: float) -> float:
        parts.append(
            f'<rect x="{left}" y="{y}" width="{inner}" height="{height:.0f}" '
            f'rx="8" fill="{_PANEL}"/>'
        )
        return y

    summary = data["summary"]
    fastest = ", ".join(summary.get("fastest_names") or []) or "none"
    method = data["method"] if data["profile"] != "mix" else "mix"
    why = _clip(str(data["route"]["why"]), 92)
    hero_h = 94
    top = panel(hero_h)
    txt(left + 12, top + 22, "RPCBench", size=16, weight="700")
    txt(
        left + 12,
        top + 40,
        f"{method} · size {data['sample_budget']} · rank {data['rank_by']}",
        fill=_DIM,
        size=11,
    )
    x = left + 12
    for label, value, fill in (
        ("Fastest", fastest, _GREEN),
        ("Primary", str(summary.get("primary") or "none"), _GREEN),
        ("Fallback", str(summary.get("fallback") or "none"), _ACCENT),
    ):
        txt(x, top + 60, label + " ", fill=_DIM, size=12, weight="700")
        x += (len(label) + 1) * 7.4
        txt(x, top + 60, value, fill=fill, size=12, weight="700")
        x += (len(value) + 3) * 7.4
    txt(left + 12, top + 78, why, fill=_DIM, size=11)
    y += hero_h + 10

    cols = [12, 40, 150, 230, 290, 350, 430]
    headers = ["#", "name", "p95", "err", "rel", "fresh", "samples"]
    rank_h = 36 + row_h * len(ranking) + 8
    top = panel(rank_h)
    txt(left + 12, top + 20, "Ranking", fill=_DIM, size=12, weight="600")
    for x, label in zip(cols, headers, strict=True):
        txt(left + x, top + 38, label, fill=_DIM, size=11)
    for i, row in enumerate(ranking):
        yy = top + 56 + i * row_h
        rank = "—" if row["rank"] is None else str(row["rank"])
        p95 = "—" if row["p95_ms"] is None else f"{row['p95_ms']:.1f}ms"
        err = "—" if row["error_rate"] is None else f"{100 * row['error_rate']:.0f}%"
        fresh = "—"
        verd = (row.get("freshness") or {}).get("verdict")
        if verd == "stale":
            fresh = "stale"
        elif verd == "fresh":
            fresh = "yes"
        name_fill = _GREEN if row.get("ok") else _RED
        if (row.get("freshness") or {}).get("verdict") == "stale":
            name_fill = _AMBER
        txt(left + cols[0], yy, rank, fill=name_fill)
        txt(left + cols[1], yy, str(row["name"]), fill=name_fill)
        txt(left + cols[2], yy, p95, fill=name_fill if row.get("p95_ms") is not None else _DIM)
        err_fill = (
            _GREEN
            if row.get("error_rate") == 0
            else (_RED if row.get("error_rate") else _DIM)
        )
        txt(left + cols[3], yy, err, fill=err_fill)
        txt(left + cols[4], yy, str(row["score"]), fill=name_fill)
        fresh_fill = (
            _GREEN if fresh == "yes" else (_AMBER if fresh == "stale" else _DIM)
        )
        txt(left + cols[5], yy, fresh, fill=fresh_fill)
        values = _sample_latencies(providers.get(row["name"]) or {})
        if len(values) >= 2:
            lo, hi = min(values), max(values)
            span = (hi - lo) or 1.0
            pts = []
            sx, sy, sw, sh = left + cols[6], yy - 12, 64, 16
            for j, value in enumerate(values):
                px = sx + 1 + j / (len(values) - 1) * (sw - 2)
                py = sy + sh - 1 - (value - lo) / span * (sh - 2)
                pts.append(f"{px:.1f},{py:.1f}")
            parts.append(
                f'<polyline fill="none" stroke="{_ACCENT}" stroke-width="1.2" '
                f'points="{" ".join(pts)}"/>'
            )
        else:
            txt(left + cols[6], yy, "—", fill=_DIM)
    y += rank_h + 10

    if step_names:
        name_w = 96
        cell_w = max(52.0, min(80.0, (inner - 20 - name_w) / len(step_names)))
        heat_h = 48 + 22 * (len(heat_rows) + 1) + 8
        top = panel(heat_h)
        txt(left + 12, top + 20, "Heatmap", fill=_DIM, size=12, weight="600")
        txt(
            left + 12,
            top + 36,
            "provider × method; green is faster ok; skip/miss is product fit",
            fill=_DIM,
            size=11,
        )
        hx = left + 12 + name_w
        for i, step in enumerate(step_names):
            txt(hx + i * cell_w + cell_w / 2, top + 54, step, fill=_DIM, size=11, anchor="middle")
        for r, (name, cells) in enumerate(heat_rows):
            yy = top + 72 + r * 22
            txt(left + 12, yy, name, size=12)
            for c, (label, color, status) in enumerate(cells):
                cx = hx + c * cell_w
                parts.append(
                    f'<rect x="{cx:.1f}" y="{yy - 11:.1f}" width="{cell_w - 6:.1f}" '
                    f'height="16" rx="3" fill="{color}"/>'
                )
                fill = _DIM if status == "skip" else _BG
                txt(
                    cx + (cell_w - 6) / 2,
                    yy,
                    label,
                    fill=fill,
                    size=10,
                    anchor="middle",
                )
        y += heat_h + 10

    sig_h = 40 + (22 * max(len(cards), 1) if cards else 28)
    top = panel(sig_h)
    txt(left + 12, top + 20, "Signals", fill=_DIM, size=12, weight="600")
    if not cards:
        txt(left + 12, top + 42, "none", fill=_DIM, size=12)
    else:
        cols = [12, 110, 190, 420]
        headers = ["name", "id", "problem", "next"]
        for x, label in zip(cols, headers, strict=True):
            txt(left + x, top + 38, label, fill=_DIM, size=11)
        for i, (name, sig) in enumerate(cards):
            yy = top + 56 + i * 18
            txt(left + cols[0], yy, _clip(str(name), 12), size=11, fill=_GREEN)
            txt(left + cols[1], yy, _clip(str(sig.get("id") or "—"), 10), size=11, fill=_ACCENT)
            txt(left + cols[2], yy, _clip(str(sig.get("problem") or "—"), 28), size=11)
            txt(left + cols[3], yy, _clip(str(sig.get("next") or "—"), 36), size=11, fill=_DIM)
    y += sig_h + 16
    height = y
    head = [
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
    return "\n".join(head + parts + ["</svg>"]) + "\n"


def shot_svgs() -> dict[str, str]:
    """Map README image name → SVG. Regenerated whenever the CLI report changes."""
    compare = demo_result()
    mix = mix_result()
    cold = cold_result()
    verbose = format_run(compare, verbose=True, color=True)
    timing = format_run(cold, verbose=True, color=True)
    return {
        "cli-compact.svg": render_svg(
            format_run(compare, verbose=False, color=True),
            "rpcbench compare --budget short",
        ),
        "cli-mix.svg": render_svg(
            format_run(mix, verbose=False, color=True),
            "rpcbench compare --profile mix --budget short",
        ),
        "cli-timing.svg": render_svg(
            _extract(timing, "Timing  (", "\nProviders"),
            "rpcbench compare --new-connection --verbose",
        ),
        "cli-verbose.svg": render_svg(
            verbose,
            "rpcbench compare --budget short --verbose",
        ),
        "html-report.svg": render_html_shot(
            compare,
            "rpcbench compare --html -o report.html",
        ),
        "html-heatmap.svg": render_html_shot(
            mix,
            "rpcbench compare --html --profile mix",
            max_signals=1,
        ),
    }


def write_shots(out: Path = OUT) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, svg in shot_svgs().items():
        path = out / name
        path.write_text(svg, encoding="utf-8")
        print(f"{path}  {svg.count(chr(10))} lines")


def main() -> None:
    write_shots()


if __name__ == "__main__":
    main()
