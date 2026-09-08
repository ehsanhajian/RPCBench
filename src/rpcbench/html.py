"""Standalone HTML compare report. Inline CSS/SVG only. Not a scanner card."""

from __future__ import annotations

from html import escape
from typing import Any

from rpcbench.report import DEFAULT_RANK_BY, DEFAULT_SIMILAR_BAND, batch_support_label, run_to_dict
from rpcbench.run import RunResult
from rpcbench.watermark import html_footer

_BG = "#0d1117"
_PANEL = "#161b22"
_FG = "#e6edf3"
_DIM = "#8b949e"
_GREEN = "#3fb950"
_RED = "#f85149"
_AMBER = "#d29922"
_BAR = "#388bfd"
_FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, Liberation Mono, monospace"


def format_html(
    result: RunResult,
    *,
    rank_by: str = DEFAULT_RANK_BY,
    similar_band: float = DEFAULT_SIMILAR_BAND,
) -> str:
    """Self-contained HTML. Same numbers as JSON. No CDN, no JS, no severity badges."""
    data = run_to_dict(result, rank_by=rank_by, similar_band=similar_band)
    ranking = data["ranking"]
    providers = {row["name"]: row for row in data["providers"]}
    body = [
        _hero(data),
        _ranking_table(ranking, providers),
        _heatmap(data),
        _signals(ranking),
        _p95_chart(ranking),
        "<!-- fold -->",
        _reliability_chart(ranking),
        _comparison_table(data["comparison"]),
        _histogram_chart(ranking, providers),
        _methods_table(data["methods"]),
        _transport_table(data),
        _batch_table(data),
        _size_scatter(data),
        _capabilities(data["capabilities"], ranking),
        _errors(ranking),
        html_footer(result),
    ]
    title = escape(f"RPCBench · {data['method']}")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8"/>\n'
        f"<title>{title}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        + "\n".join(part for part in body if part)
        + "\n</body>\n</html>\n"
    )


def _hero(data: dict[str, Any]) -> str:
    summary = data["summary"]
    fastest = summary.get("fastest_names") or []
    mark = data["watermark"]
    method = data["method"] if data["profile"] != "mix" else "mix"
    return (
        '<header class="hero">'
        "<h1>RPCBench</h1>"
        f"<p class=\"meta\">{escape(str(method))} · size {escape(str(data['sample_budget']))} · "
        f"rank {escape(str(data['rank_by']))} · similar {100 * data['similar_band']:.0f}% · "
        f"sha={escape(str(mark['git_sha'] or '—'))}</p>"
        '<p class="winner">'
        f'<span class="label">Fastest</span> {_name_list(fastest, "ok")} · '
        f'<span class="label">Primary</span> {_one_name(summary.get("primary"), "ok")} · '
        f'<span class="label">Fallback</span> {_one_name(summary.get("fallback"), "accent")}'
        "</p>"
        f'<p class="why">{escape(data["route"]["why"])}</p>'
        "</header>"
    )


def _ranking_table(
    ranking: list[dict[str, Any]],
    providers: dict[str, dict[str, Any]],
) -> str:
    rows = []
    for row in ranking:
        rank = "—" if row["rank"] is None else str(row["rank"])
        spark = _sparkline(_sample_latencies(providers.get(row["name"]) or {}))
        tone = _row_tone(row)
        rank_cls = "ok" if row["rank"] == 1 else ("bad" if not row["ok"] else "dim")
        rows.append(
            "<tr>"
            f'<td class="num {rank_cls}">{escape(rank)}</td>'
            f'<td class="{tone}">{escape(row["name"])}</td>'
            f'<td class="num {tone}">{_ms(row["p95_ms"])}</td>'
            f'<td class="num {_err_tone(row.get("error_rate"))}">{_pct(row.get("error_rate"))}</td>'
            f'<td class="num {tone}">{row["score"]}</td>'
            f"<td>{_fresh_html(row.get('freshness'))}</td>"
            f'<td class="spark">{spark}</td>'
            "</tr>"
        )
    return (
        '<section aria-label="ranking">'
        "<h2>Ranking</h2>"
        "<table>"
        "<thead><tr>"
        '<th class="num">#</th><th>name</th>'
        '<th class="num">p95</th><th class="num">err</th><th class="num">rel</th>'
        "<th>fresh</th><th>samples</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _heatmap(data: dict[str, Any]) -> str:
    step_names, rows = _heatmap_grid(data)
    if not step_names:
        return ""
    head = "".join(f"<th>{escape(name)}</th>" for name in step_names)
    body = []
    for name, cells in rows:
        tds = "".join(
            f'<td class="heat {escape(status)}" style="background:{color}">{escape(label)}</td>'
            for label, color, status in cells
        )
        body.append(f'<tr><th class="heat-name">{escape(name)}</th>{tds}</tr>')
    return (
        '<section aria-label="heatmap">'
        "<h2>Heatmap</h2>"
        '<p class="meta">provider × method; green is faster ok; skip/miss is product fit</p>'
        '<table class="heat">'
        f"<thead><tr><th class=\"heat-name\"></th>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table>"
        "</section>"
    )


def _signals(ranking: list[dict[str, Any]]) -> str:
    rows = []
    for row in ranking:
        for sig in (row.get("verdict") or {}).get("signals") or []:
            rows.append(
                "<tr>"
                f'<td class="{_row_tone(row)}">{escape(row["name"])}</td>'
                f'<td class="accent">{escape(str(sig.get("id") or "—"))}</td>'
                f'<td class="wrap">{escape(str(sig.get("problem") or "—"))}</td>'
                f'<td class="wrap">{escape(str(sig.get("why") or "—"))}</td>'
                f'<td class="wrap">{escape(str(sig.get("next") or "—"))}</td>'
                "</tr>"
            )
    if not rows:
        body = '<p class="dim">none</p>'
    else:
        body = (
            "<table>"
            "<thead><tr>"
            "<th>name</th><th>id</th><th>problem</th><th>why</th><th>next</th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
        )
    return (
        '<section aria-label="signals">'
        "<h2>Signals</h2>"
        '<p class="meta">problem / why / next; routing and config, not hardening</p>'
        f"{body}"
        "</section>"
    )


def _heatmap_grid(
    data: dict[str, Any],
) -> tuple[list[str], list[tuple[str, list[tuple[str, str, str]]]]]:
    cover = data.get("coverage") or {}
    steps = cover.get("steps") or []
    if not steps:
        return [], []
    by_name = {row["name"]: row for row in cover.get("providers") or []}
    names = [row["name"] for row in data["ranking"]]
    ok_p95 = [
        p95
        for name in names
        for step in steps
        if (p95 := _step_p95(data, name, step["name"], len(steps))) is not None
        and ((by_name.get(name) or {}).get("cells") or {})
        .get(step["name"], {})
        .get("status")
        == "ok"
    ]
    lo = min(ok_p95) if ok_p95 else 0.0
    hi = max(ok_p95) if ok_p95 else 0.0
    rows: list[tuple[str, list[tuple[str, str, str]]]] = []
    for name in names:
        prov = by_name.get(name) or {"cells": {}}
        cells: list[tuple[str, str, str]] = []
        for step in steps:
            cell = (prov.get("cells") or {}).get(step["name"]) or {}
            status = cell.get("status") or "skip"
            p95 = _step_p95(data, name, step["name"], len(steps))
            color = _heat_color(status, p95, lo, hi)
            if status == "ok" and p95 is not None:
                label = f"{p95:.0f}"
            elif status == "skip":
                label = "skip"
            else:
                label = str(cell.get("error_class") or status)
            cells.append((label, color, status))
        rows.append((name, cells))
    return [step["name"] for step in steps], rows


def _step_p95(
    data: dict[str, Any], name: str, step: str, n_steps: int
) -> float | None:
    for row in data.get("methods") or []:
        if row["name"] == name and row["step"] == step:
            return row.get("p95_ms")
    if n_steps == 1:
        for row in data["ranking"]:
            if row["name"] == name:
                return row.get("p95_ms")
    return None


def _heat_color(
    status: str, p95: float | None, lo: float, hi: float
) -> str:
    if status == "skip":
        return "#30363d"
    if status != "ok":
        return _RED
    if p95 is None or hi <= lo:
        return _GREEN
    t = max(0.0, min(1.0, (p95 - lo) / (hi - lo)))
    # Faster → green; slower ok → amber. Not a severity badge.
    r = int(63 + t * (210 - 63))
    g = int(185 + t * (140 - 185))
    b = int(80 + t * (20 - 80))
    return f"#{r:02x}{g:02x}{b:02x}"


def _sample_latencies(provider: dict[str, Any]) -> list[float]:
    out: list[float] = []
    for hit in provider.get("samples") or []:
        if hit.get("ok") and hit.get("latency_ms") is not None:
            out.append(float(hit["latency_ms"]))
    return out


def _sparkline(values: list[float]) -> str:
    if len(values) < 2:
        return "—"
    width, height = 64, 16
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pts = []
    for i, value in enumerate(values):
        x = 1 + i / (len(values) - 1) * (width - 2)
        y = height - 1 - (value - lo) / span * (height - 2)
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg class="spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" aria-hidden="true">'
        f'<polyline fill="none" stroke="{_BAR}" stroke-width="1.2" '
        f'points="{" ".join(pts)}"/></svg>'
    )


def _comparison_table(comparison: list[dict[str, Any]]) -> str:
    rows = []
    for row in comparison:
        tone = _row_tone(row)
        rows.append(
            "<tr>"
            f'<td class="{tone}">{escape(row["name"])}</td>'
            f'<td class="num {tone}">{_ms(row["p95_ms"])}</td>'
            f'<td class="num {_err_tone(row.get("error_rate"))}">{_pct(row.get("error_rate"))}</td>'
            f'<td class="num {tone}">{row["reliability"]["score"]}</td>'
            f"<td>{_fresh_html(row.get('freshness'))}</td>"
            f"<td>{_match_html(row.get('consistency'))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="comparison">'
        "<h2>Comparison</h2>"
        "<table>"
        "<thead><tr>"
        '<th>name</th><th class="num">p95</th><th class="num">err</th>'
        '<th class="num">rel</th><th>fresh</th><th>match</th>'
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _methods_table(methods: list[dict[str, Any]]) -> str:
    if not methods:
        return ""
    rows = []
    for row in methods:
        err = row.get("error_rate")
        tone = "ok" if row.get("n_ok") else ("bad" if row.get("n_fail") else "dim")
        rows.append(
            "<tr>"
            f'<td class="{tone}">{escape(row["name"])}</td>'
            f"<td>{escape(row['method'])}</td>"
            f'<td class="num {tone}">{_ms(row["p95_ms"])}</td>'
            f'<td class="num {_err_tone(err)}">{_pct(err)}</td>'
            f'<td class="num">{_bytes(row.get("bytes_in_p95"))}</td>'
            "</tr>"
        )
    return (
        '<section aria-label="methods">'
        "<h2>Methods</h2>"
        "<table>"
        "<thead><tr>"
        '<th>name</th><th>method</th>'
        '<th class="num">p95</th><th class="num">err</th><th class="num">in p95</th>'
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _capabilities(cap: dict[str, Any], ranking: list[dict[str, Any]]) -> str:
    method = str(cap.get("method") or "—")
    missed = {
        str(row.get("name") or ""): str(row.get("error_class") or "error")
        for row in cap.get("missed") or []
    }
    rows = []
    for row in ranking:
        name = str(row.get("name") or "")
        cls = missed.get(name)
        ok = cls is None and bool(row.get("ok"))
        tone = "ok" if ok else "bad"
        rows.append(
            "<tr>"
            f'<td class="{tone}">{escape(name)}</td>'
            f'<td class="{tone}">{"yes" if ok else "no"}</td>'
            f'<td class="{"dim" if ok else "bad"}">{escape(cls or "—")}</td>'
            "</tr>"
        )
    table = ""
    if rows:
        table = (
            "<table>"
            "<thead><tr><th>name</th><th>responded</th><th>class</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
        )
    return (
        '<section aria-label="capabilities">'
        "<h2>Capabilities</h2>"
        f'<p class="meta">{escape(method)} · {cap.get("responded")}/{cap.get("total")} responded</p>'
        f"{table}"
        "</section>"
    )


def _errors(ranking: list[dict[str, Any]]) -> str:
    rows = []
    for row in ranking:
        by = row.get("errors") or {}
        if not by:
            continue
        tone = _row_tone(row)
        for cls, n in by.items():
            rows.append(
                "<tr>"
                f'<td class="{tone}">{escape(row["name"])}</td>'
                f'<td class="bad">{escape(str(cls))}</td>'
                f'<td class="num">{escape(str(n))}</td>'
                "</tr>"
            )
    if not rows:
        body = '<p class="dim">none</p>'
    else:
        body = (
            "<table>"
            "<thead><tr>"
            '<th>name</th><th>class</th><th class="num">n</th>'
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
        )
    return (
        '<section aria-label="errors">'
        "<h2>Errors</h2>"
        '<p class="meta">timed-sample error classes</p>'
        f"{body}"
        "</section>"
    )


def _p95_chart(ranking: list[dict[str, Any]]) -> str:
    items = [(row["name"], row["p95_ms"], bool(row["ok"])) for row in ranking]
    return _h_bars(items, title="P95", unit="ms")


def _reliability_chart(ranking: list[dict[str, Any]]) -> str:
    items = [(row["name"], float(row["score"]), bool(row["ok"])) for row in ranking]
    return _h_bars(items, title="Reliability 0–100", unit="", cap=100.0)


def _histogram_chart(
    ranking: list[dict[str, Any]],
    providers: dict[str, dict[str, Any]],
) -> str:
    labels: list[str] = []
    series: list[tuple[str, list[int]]] = []
    for row in ranking:
        prov = providers.get(row["name"]) or {}
        hist = (prov.get("performance") or {}).get("histogram") or []
        if not labels and hist:
            labels = [str(bucket["label"]) for bucket in hist]
        vals = [int(bucket["n"]) for bucket in hist]
        if sum(vals):
            series.append((row["name"], vals))
    if not labels or not series:
        return (
            '<section aria-label="histogram"><h2>Histogram</h2>'
            "<p class=\"meta\">no successful samples</p></section>"
        )
    n_g = len(series)
    n_b = len(labels)
    left = 36
    top = 16
    group_w = max(56.0, 18.0 + n_g * 10.0)
    plot_w = n_b * group_w
    plot_h = 100
    legend_h = 16 + 16 * n_g
    width = max(left + plot_w + 12, 280)
    height = top + plot_h + 28 + legend_h
    peak = max((n for _, vals in series for n in vals), default=1) or 1
    bar_w = max(6.0, min(14.0, (group_w - 12) / max(n_g, 1)))
    cluster = n_g * bar_w
    parts = [
        '<section aria-label="histogram"><h2>Histogram</h2>'
        '<p class="meta">successful samples per latency bucket</p>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="latency histogram">'
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" '
        f'stroke="#30363d" stroke-width="1"/>'
        f'<text x="8" y="{top + 8}" fill="{_DIM}" font-size="10" '
        f'font-family="{_FONT}">{peak}</text>'
        f'<text x="8" y="{top + plot_h}" fill="{_DIM}" font-size="10" '
        f'font-family="{_FONT}">0</text>'
    ]
    palette = (_BAR, _GREEN, "#d2a8ff", "#ffa657", _RED, "#79c0ff", "#f778ba", "#e3b341")
    for gi, (name, vals) in enumerate(series):
        color = palette[gi % len(palette)]
        for bi, n in enumerate(vals):
            if n <= 0:
                continue
            h = max(2.0, (n / peak) * (plot_h - 6))
            x = left + bi * group_w + (group_w - cluster) / 2 + gi * bar_w
            y = top + plot_h - h
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w - 1.5:.1f}" height="{h:.1f}" '
                f'rx="1.5" fill="{color}">'
                f"<title>{escape(name)} {escape(labels[bi])} {n}</title></rect>"
            )
    for bi, label in enumerate(labels):
        x = left + bi * group_w + group_w / 2
        parts.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 16:.0f}" text-anchor="middle" '
            f'fill="{_DIM}" font-size="11" font-family="{_FONT}">{escape(label)}</text>'
        )
    ly = top + plot_h + 32
    for gi, (name, _) in enumerate(series):
        color = palette[gi % len(palette)]
        yy = ly + gi * 16
        parts.append(
            f'<rect x="{left}" y="{yy - 8:.0f}" width="8" height="8" rx="1" fill="{color}"/>'
            f'<text x="{left + 14}" y="{yy:.0f}" fill="{_FG}" font-size="11" '
            f'dominant-baseline="central" font-family="{_FONT}">{escape(name)}</text>'
        )
    parts.append("</svg></section>")
    return "".join(parts)


def _h_bars(
    items: list[tuple[str, float | None, bool]],
    *,
    title: str,
    unit: str,
    cap: float | None = None,
) -> str:
    known = [value for _, value, ok in items if value is not None and ok]
    peak = cap if cap is not None else (max(known) if known else 1.0)
    if peak <= 0:
        peak = 1.0
    name_w = max((len(name) for name, _, _ in items), default=4)
    left = max(92.0, min(148.0, 16 + name_w * 7.6))
    top = 6
    bar_h = 14
    gap = 8
    plot_w = 280
    row_h = bar_h + gap
    height = top + max(len(items), 1) * row_h + 4
    width = left + plot_w + 80
    parts = [
        f'<section aria-label="{escape(title.lower())}"><h2>{escape(title)}</h2>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="{escape(title)}">'
    ]
    for i, (name, value, ok) in enumerate(items):
        y = top + i * row_h
        mid = y + bar_h / 2
        name_fill = _GREEN if ok else _RED
        parts.append(
            f'<text x="8" y="{mid:.1f}" fill="{name_fill}" font-size="12" '
            f'dominant-baseline="central" font-family="{_FONT}">{escape(name)}</text>'
        )
        empty = value is None or (not ok and (value or 0) == 0)
        if empty:
            parts.append(
                f'<text x="{left}" y="{mid:.1f}" fill="{_DIM}" font-size="12" '
                f'dominant-baseline="central" font-family="{_FONT}">—</text>'
            )
            continue
        w = max(4.0, (value / peak) * plot_w)
        color = _GREEN if ok else _RED
        label = f"{value:.1f}{unit}" if unit else f"{value:.0f}"
        parts.append(
            f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="3" fill="{color}"/>'
            f'<text x="{left + w + 8:.1f}" y="{mid:.1f}" fill="{_FG}" font-size="12" '
            f'dominant-baseline="central" font-family="{_FONT}">{escape(label)}</text>'
        )
    parts.append("</svg></section>")
    return "".join(parts)


def _transport_table(data: dict[str, Any]) -> str:
    rows = []
    for row in data.get("ranking") or []:
        summary = row.get("transport")
        if not summary:
            continue
        enc = str(summary.get("encoding") or "—")
        enc_cls = "ok" if enc != "—" else "dim"
        rows.append(
            "<tr>"
            f"<td>{escape(str(row['name']))}</td>"
            f"<td>{escape(str(summary.get('http_version') or '—'))}</td>"
            f'<td class="{enc_cls}">{escape(enc)}</td>'
            f'<td class="num">{_bytes(summary.get("bytes_out_mean"))}</td>'
            f'<td class="num">{_bytes(summary.get("bytes_in_mean"))}</td>'
            f'<td class="num">{_bytes(summary.get("bytes_in_p95"))}</td>'
            "</tr>"
        )
    if not rows:
        return ""
    return (
        '<section aria-label="transport">'
        "<h2>Transport</h2>"
        '<p class="meta">negotiated proto, content-encoding, wire bytes; not mixed into ranking</p>'
        "<table>"
        "<thead><tr>"
        '<th>name</th><th>proto</th><th>enc</th>'
        '<th class="num">out</th><th class="num">in</th><th class="num">in p95</th>'
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _batch_table(data: dict[str, Any]) -> str:
    if int(data.get("batch") or 0) <= 0:
        return ""
    rows = []
    for row in data.get("ranking") or []:
        summary = row.get("batch")
        if not summary:
            continue
        support = batch_support_label(summary)
        if support == "yes":
            tone = "ok"
        elif support == "no":
            tone = "bad"
        elif support == "partial":
            tone = "stale"
        else:
            tone = "dim"
        items = (
            str(summary.get("error_class") or "—")
            if support in {"no", "skip"}
            else f"{summary.get('n_ok')}/{summary.get('size')}"
        )
        ratio = summary.get("ratio")
        ratio_txt = "—" if ratio is None else f"{float(ratio):.1f}×"
        rows.append(
            "<tr>"
            f'<td class="{tone}">{escape(str(row["name"]))}</td>'
            f'<td class="{tone}">{escape(support)}</td>'
            f'<td class="num">{_ms(summary.get("batch_ms"))}</td>'
            f'<td class="num">{_ms(summary.get("serial_ms"))}</td>'
            f'<td class="num">{escape(ratio_txt)}</td>'
            f"<td>{escape(str(items))}</td>"
            "</tr>"
        )
    if not rows:
        return ""
    size = data.get("batch")
    return (
        '<section aria-label="batch">'
        "<h2>Batch</h2>"
        f'<p class="meta">{escape(str(size))} calls in one POST vs the same '
        f"{escape(str(size))} sent one-by-one; not mixed into ranking</p>"
        "<table>"
        "<thead><tr>"
        "<th>name</th><th>support</th>"
        '<th class="num">batch</th><th class="num">serial</th>'
        '<th class="num">ratio</th><th>items</th>'
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _size_scatter(data: dict[str, Any]) -> str:
    points: list[tuple[float, float, str, str]] = []
    method_default = str(data.get("method") or "")
    for prov in data.get("providers") or []:
        name = str(prov.get("name") or "")
        for hit in prov.get("samples") or []:
            if not hit.get("ok"):
                continue
            size = hit.get("bytes_in")
            latency = hit.get("latency_ms")
            if size is None or latency is None:
                continue
            method = str(hit.get("method") or method_default)
            points.append((float(size), float(latency), method, name))
    if not points:
        return ""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    xmin, xmax = min(xs), max(xs)
    ymax = max(ys) or 1.0
    has_logs = any(method == "eth_getLogs" for _, _, method, _ in points)
    # Head-only runs are ~40–80B; a blob of dots teaches nothing.
    if not has_logs and xmax < 1024 and (xmax - xmin) < 256:
        return ""
    left, top, plot_w, plot_h = 56, 20, 360, 140
    width = left + plot_w + 16
    height = top + plot_h + 36
    span = xmax if xmax > 0 else 1.0
    parts = [
        '<section aria-label="size vs latency">'
        "<h2>Size vs latency</h2>"
        '<p class="meta">wire bytes vs RTT; a fat getLogs sitting high is the payload, '
        "not a slow node; logs are brighter</p>"
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="size vs latency">'
        f'<rect width="100%" height="100%" fill="{_PANEL}"/>'
        f'<text x="8" y="{top + 10}" fill="{_DIM}" font-size="10" '
        f'font-family="{_FONT}">{ymax:.0f}ms</text>'
        f'<text x="8" y="{top + plot_h}" fill="{_DIM}" font-size="10" '
        f'font-family="{_FONT}">0ms</text>'
    ]
    for size, latency, method, name in points:
        x = left + (size / span) * (plot_w - 8)
        y = top + plot_h - (latency / ymax) * (plot_h - 8)
        color = _GREEN if method == "eth_getLogs" else _BAR
        r = 4 if method == "eth_getLogs" else 3
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{color}">'
            f"<title>{escape(name)} {escape(method)} {size:.0f}B {latency:.1f}ms</title>"
            "</circle>"
        )
    parts.append(
        f'<text x="{left}" y="{top + plot_h + 16}" fill="{_DIM}" font-size="11" '
        f'font-family="{_FONT}">0B</text>'
        f'<text x="{left + plot_w}" y="{top + plot_h + 16}" text-anchor="end" '
        f'fill="{_DIM}" font-size="11" font-family="{_FONT}">{_bytes(xmax)}</text>'
        "</svg></section>"
    )
    return "".join(parts)


def _bytes(value: float | None) -> str:
    if value is None:
        return "—"
    n = int(round(value))
    if n < 1000:
        return f"{n}B"
    if n < 1_000_000:
        return f"{n / 1000:.1f}kB"
    return f"{n / 1_000_000:.1f}MB"


def _fresh_cell(fresh: dict[str, Any] | None) -> str:
    if not fresh:
        return "—"
    verdict = fresh.get("verdict")
    if verdict == "stale":
        return "stale"
    if verdict == "fresh":
        return "yes"
    return "—"


def _fresh_html(fresh: dict[str, Any] | None) -> str:
    text = _fresh_cell(fresh)
    verd = (fresh or {}).get("verdict") if fresh else None
    cls = "ok" if verd == "fresh" else ("stale" if verd == "stale" else "dim")
    return f'<span class="{cls}">{escape(text)}</span>'


def _match_html(cons: dict[str, Any] | None) -> str:
    text = _match_cell(cons)
    verd = (cons or {}).get("verdict") if cons else None
    cls = "ok" if verd == "agree" else ("bad" if verd == "disagree" else "dim")
    return f'<span class="{cls}">{escape(text)}</span>'


def _name_list(names: list[str], cls: str) -> str:
    if not names:
        return '<span class="dim">none</span>'
    return ", ".join(f'<span class="{cls}">{escape(name)}</span>' for name in names)


def _one_name(name: str | None, cls: str) -> str:
    if not name:
        return '<span class="dim">none</span>'
    return f'<span class="{cls}">{escape(str(name))}</span>'


def _row_tone(row: dict[str, Any]) -> str:
    if not row.get("ok"):
        return "bad"
    if (row.get("freshness") or {}).get("verdict") == "stale":
        return "stale"
    if "rank" in row and row.get("rank") is None:
        decision = ((row.get("verdict") or {}).get("decision") or "")
        if decision == "not_ready":
            return "bad"
        return "stale"
    return "ok"


def _err_tone(err: float | None) -> str:
    if err is None:
        return "dim"
    if err == 0:
        return "ok"
    return "bad"


def _match_cell(cons: dict[str, Any] | None) -> str:
    if not cons:
        return "—"
    verdict = cons.get("verdict")
    if verdict == "agree":
        return "yes"
    if verdict == "disagree":
        return "no"
    return "—"


def _ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}ms"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{100 * value:.0f}%"


_CSS = f"""
:root {{ color-scheme: dark; }}
body {{
  margin: 0 auto; max-width: 960px; padding: 20px 16px 48px;
  background: {_BG}; color: {_FG}; font: 13px/1.45 {_FONT};
}}
h1 {{ font-size: 18px; margin: 0 0 8px; color: {_FG}; }}
h2 {{ font-size: 13px; color: {_DIM}; margin: 0 0 8px; font-weight: 600; }}
.meta, .why {{ color: {_DIM}; margin: 0 0 8px; }}
.label, .dim {{ color: {_DIM}; }}
.label {{ font-weight: 600; }}
.winner {{ margin: 12px 0 8px; color: {_FG}; }}
.ok {{ color: {_GREEN}; }}
.bad {{ color: {_RED}; }}
.stale {{ color: {_AMBER}; }}
.accent {{ color: {_BAR}; }}
.hero, section, footer {{
  background: {_PANEL}; border-radius: 8px; padding: 12px 14px; margin: 0 0 12px;
}}
table {{ width: auto; max-width: 100%; border-collapse: collapse; }}
th, td {{
  text-align: left; padding: 6px 20px 6px 0; vertical-align: middle;
  white-space: nowrap;
}}
th {{ color: {_DIM}; font-weight: 500; }}
th.num, td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
td.spark {{ width: 72px; padding-right: 0; }}
td.wrap {{ white-space: normal; max-width: 220px; }}
svg {{ display: block; max-width: 100%; }}
svg.spark {{ display: inline-block; vertical-align: middle; }}
table.heat {{
  width: auto; border-collapse: separate; border-spacing: 4px 4px;
}}
table.heat th, table.heat td.heat {{
  text-align: center; padding: 0 10px; height: 28px; line-height: 28px;
  font-size: 11px; font-variant-numeric: tabular-nums; white-space: nowrap;
  vertical-align: middle;
}}
table.heat th.heat-name, table.heat tbody th.heat-name {{
  text-align: left; padding: 0 12px 0 0; color: {_FG}; font-weight: 500;
}}
td.heat {{ color: {_BG}; min-width: 72px; border-radius: 4px; }}
td.heat.skip {{ color: {_DIM}; }}
footer {{ color: {_DIM}; }}
footer a {{ color: {_BAR}; }}
@media print {{
  :root {{ color-scheme: light; }}
  * {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  body {{ background: #fff; color: #111; }}
  h2, .meta, .why, .label, .dim, footer, th {{ color: #444; }}
  .ok {{ color: #1a7f37; }}
  .bad {{ color: #cf222e; }}
  .stale {{ color: #9a6700; }}
  .accent {{ color: #0969da; }}
  .hero, section, footer {{
    background: #fff; color: #111; break-inside: avoid;
  }}
  svg {{ break-inside: avoid; }}
  @page {{ margin: 12mm; }}
}}
"""
