"""Standalone HTML compare report. Inline CSS/SVG only. Not a scanner card."""

from __future__ import annotations

from html import escape
from typing import Any

from rpcbench.recommend import Route, html_block as route_html
from rpcbench.report import DEFAULT_RANK_BY, DEFAULT_SIMILAR_BAND, run_to_dict
from rpcbench.run import RunResult
from rpcbench.verdict import Signal, Verdict, html_block as verdict_html
from rpcbench.watermark import html_footer

_BG = "#0d1117"
_PANEL = "#161b22"
_FG = "#e6edf3"
_DIM = "#8b949e"
_GREEN = "#3fb950"
_RED = "#f85149"
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
        _ranking_table(ranking),
        _p95_chart(ranking),
        "<!-- fold -->",
        _reliability_chart(ranking),
        _comparison_table(data["comparison"]),
        _histogram_chart(ranking, providers),
        _methods_table(data["methods"]),
        _capabilities(data["capabilities"]),
        _errors(ranking),
        verdict_html(_verdict_rows(ranking)),
        route_html(
            Route(
                data["route"]["primary"],
                data["route"]["fallback"],
                data["route"]["why"],
            )
        ),
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
    fastest_txt = ", ".join(fastest) if fastest else "none"
    mark = data["watermark"]
    method = data["method"] if data["profile"] != "mix" else "mix"
    return (
        '<header class="hero">'
        "<h1>RPCBench</h1>"
        f"<p class=\"meta\">{escape(str(method))} · size {escape(str(data['sample_budget']))} · "
        f"rank {escape(str(data['rank_by']))} · similar {100 * data['similar_band']:.0f}% · "
        f"sha={escape(str(mark['git_sha'] or '—'))}</p>"
        '<p class="winner">'
        f"<strong>Fastest</strong> {escape(fastest_txt)} · "
        f"<strong>Primary</strong> {escape(str(summary.get('primary') or 'none'))} · "
        f"<strong>Fallback</strong> {escape(str(summary.get('fallback') or 'none'))}"
        "</p>"
        f'<p class="why">{escape(data["route"]["why"])}</p>'
        "</header>"
    )


def _ranking_table(ranking: list[dict[str, Any]]) -> str:
    rows = []
    for row in ranking:
        rank = "—" if row["rank"] is None else str(row["rank"])
        rows.append(
            "<tr>"
            f"<td>{escape(rank)}</td>"
            f"<td>{escape(row['name'])}</td>"
            f"<td class=\"num\">{_ms(row['p95_ms'])}</td>"
            f"<td class=\"num\">{_pct(row['error_rate'])}</td>"
            f"<td class=\"num\">{row['score']}</td>"
            f"<td>{escape(_fresh_cell(row.get('freshness')))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="ranking">'
        "<h2>Ranking</h2>"
        "<table>"
        "<thead><tr>"
        "<th>#</th><th>name</th><th>p95</th><th>err</th><th>rel</th><th>fresh</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _comparison_table(comparison: list[dict[str, Any]]) -> str:
    rows = []
    for row in comparison:
        rows.append(
            "<tr>"
            f"<td>{escape(row['name'])}</td>"
            f"<td class=\"num\">{_ms(row['p95_ms'])}</td>"
            f"<td class=\"num\">{_pct(row['error_rate'])}</td>"
            f"<td class=\"num\">{row['reliability']['score']}</td>"
            f"<td>{escape(_fresh_cell(row.get('freshness')))}</td>"
            f"<td>{escape(_match_cell(row.get('consistency')))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="comparison">'
        "<h2>Comparison</h2>"
        "<table>"
        "<thead><tr>"
        "<th>name</th><th>p95</th><th>err</th><th>rel</th><th>fresh</th><th>match</th>"
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
        rows.append(
            "<tr>"
            f"<td>{escape(row['name'])}</td>"
            f"<td>{escape(row['method'])}</td>"
            f"<td class=\"num\">{_ms(row['p95_ms'])}</td>"
            f"<td class=\"num\">{_pct(row['error_rate'])}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="methods">'
        "<h2>Methods</h2>"
        "<table>"
        "<thead><tr><th>name</th><th>method</th><th>p95</th><th>err</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _capabilities(cap: dict[str, Any]) -> str:
    missed = cap.get("missed") or []
    bits = ", ".join(
        f"{row['name']} ({row['error_class'] or 'error'})" for row in missed
    )
    extra = f"<p>missed {escape(bits)}</p>" if bits else ""
    return (
        '<section aria-label="capabilities">'
        "<h2>Capabilities</h2>"
        f"<p>{escape(str(cap['method']))} · {cap['responded']}/{cap['total']} responded</p>"
        f"{extra}"
        "</section>"
    )


def _errors(ranking: list[dict[str, Any]]) -> str:
    rows = []
    for row in ranking:
        by = row.get("errors") or {}
        if not by:
            continue
        bits = ", ".join(f"{cls}={n}" for cls, n in by.items())
        rows.append(f"<li>{escape(row['name'])} · {escape(bits)}</li>")
    if not rows:
        return (
            '<section aria-label="errors"><h2>Errors</h2><p>none</p></section>'
        )
    return (
        '<section aria-label="errors"><h2>Errors</h2>'
        f"<ul>{''.join(rows)}</ul></section>"
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
        series.append((row["name"], [int(bucket["n"]) for bucket in hist]))
    if not labels or not any(sum(vals) for _, vals in series):
        return (
            '<section aria-label="histogram"><h2>Histogram</h2>'
            "<p>no successful samples</p></section>"
        )
    n_g = len(series)
    n_b = len(labels)
    left = 72
    top = 28
    plot_w = max(280, n_b * max(36, n_g * 12 + 16))
    plot_h = 120
    width = left + plot_w + 16
    height = top + plot_h + 36
    peak = max((n for _, vals in series for n in vals), default=1) or 1
    group_w = plot_w / n_b
    bar_w = max(4.0, (group_w - 8) / max(n_g, 1))
    parts = [
        f'<section aria-label="histogram"><h2>Histogram</h2>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="latency histogram">'
        f'<rect width="100%" height="100%" fill="{_PANEL}"/>'
    ]
    palette = (_BAR, _GREEN, "#d2a8ff", "#ffa657", _RED)
    for gi, (name, vals) in enumerate(series):
        color = palette[gi % len(palette)]
        for bi, n in enumerate(vals):
            h = (n / peak) * (plot_h - 4)
            x = left + bi * group_w + 4 + gi * bar_w
            y = top + plot_h - h
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w - 1:.1f}" height="{h:.1f}" '
                f'fill="{color}"><title>{escape(name)} {escape(labels[bi])} {n}</title></rect>'
            )
    for bi, label in enumerate(labels):
        x = left + bi * group_w + group_w / 2
        parts.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 16:.0f}" text-anchor="middle" '
            f'fill="{_DIM}" font-size="11" font-family="{_FONT}">{escape(label)}</text>'
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
    known = [value for _, value, _ in items if value is not None]
    peak = cap if cap is not None else (max(known) if known else 1.0)
    if peak <= 0:
        peak = 1.0
    left = 100
    top = 8
    bar_h = 16
    gap = 10
    plot_w = 320
    height = top + max(len(items), 1) * (bar_h + gap) + 8
    width = left + plot_w + 72
    parts = [
        f'<section aria-label="{escape(title.lower())}"><h2>{escape(title)}</h2>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">'
        f'<rect width="100%" height="100%" fill="{_PANEL}"/>'
    ]
    for i, (name, value, ok) in enumerate(items):
        y = top + i * (bar_h + gap)
        color = _GREEN if ok else _RED
        parts.append(
            f'<text x="8" y="{y + 13}" fill="{_FG}" font-size="12" '
            f'font-family="{_FONT}">{escape(name)}</text>'
        )
        if value is None:
            parts.append(
                f'<text x="{left}" y="{y + 13}" fill="{_DIM}" font-size="12" '
                f'font-family="{_FONT}">—</text>'
            )
            continue
        w = max(2.0, (value / peak) * plot_w)
        label = f"{value:.1f}{unit}" if unit else f"{value:.0f}"
        parts.append(
            f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" fill="{color}"/>'
            f'<text x="{left + w + 6:.1f}" y="{y + 13}" fill="{_FG}" font-size="12" '
            f'font-family="{_FONT}">{escape(label)}</text>'
        )
    parts.append("</svg></section>")
    return "".join(parts)


def _verdict_rows(ranking: list[dict[str, Any]]) -> list[tuple[str, Verdict]]:
    rows: list[tuple[str, Verdict]] = []
    for row in ranking:
        raw = row["verdict"]
        signals = tuple(
            Signal(item["id"], item["problem"], item["why"], item["next"])
            for item in raw["signals"]
        )
        rows.append((row["name"], Verdict(raw["decision"], raw["kind"], signals)))
    return rows


def _fresh_cell(fresh: dict[str, Any] | None) -> str:
    if not fresh:
        return "—"
    verdict = fresh.get("verdict")
    if verdict == "stale":
        return "stale"
    if verdict == "fresh":
        return "yes"
    return "—"


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
h1 {{ font-size: 18px; margin: 0 0 8px; }}
h2 {{ font-size: 13px; color: {_DIM}; margin: 20px 0 8px; font-weight: 600; }}
.meta, .why {{ color: {_DIM}; margin: 0 0 8px; }}
.winner {{ margin: 12px 0 8px; }}
.hero, section, footer {{
  background: {_PANEL}; border-radius: 8px; padding: 12px 14px; margin: 0 0 12px;
}}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ text-align: left; padding: 4px 8px 4px 0; }}
th {{ color: {_DIM}; font-weight: 500; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
svg {{ display: block; max-width: 100%; }}
footer {{ color: {_DIM}; }}
footer a {{ color: {_BAR}; }}
ul {{ margin: 0; padding-left: 18px; }}
article h3 {{ margin: 0 0 4px; font-size: 13px; }}
dl {{ margin: 6px 0; }}
dt {{ color: {_DIM}; }}
dd {{ margin: 0 0 4px; }}
"""
