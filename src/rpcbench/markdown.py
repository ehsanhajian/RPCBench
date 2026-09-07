"""GitHub-flavored markdown compare report. Not a finding card."""

from __future__ import annotations

from typing import Any

from rpcbench.report import DEFAULT_RANK_BY, DEFAULT_SIMILAR_BAND, run_to_dict
from rpcbench.run import RunResult
from rpcbench.verdict import NOT_READY, READY, RISKY
from rpcbench.watermark import DOCS_BOUNDARY, DOCS_METHODOLOGY

_DECISION = {READY: "ready", RISKY: "risky", NOT_READY: "not ready"}


def format_md(
    result: RunResult,
    *,
    rank_by: str = DEFAULT_RANK_BY,
    similar_band: float = DEFAULT_SIMILAR_BAND,
) -> str:
    """Pasteable GitHub markdown. Same numbers as JSON. No severity badges."""
    return format_md_dict(
        run_to_dict(result, rank_by=rank_by, similar_band=similar_band)
    )


def format_md_dict(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    fastest = summary.get("fastest_names") or []
    fastest_txt = ", ".join(str(name) for name in fastest) if fastest else "none"
    method = data.get("method") if data.get("profile") != "mix" else "mix"
    band = data.get("similar_band")
    band_txt = f"{100 * band:.0f}%" if isinstance(band, (int, float)) else "—"
    mark = data.get("watermark") or {}
    sha = mark.get("git_sha") or "—"
    ranking_rows: list[list[str]] = []
    for row in data.get("ranking") or []:
        rank = "—" if row.get("rank") is None else str(row["rank"])
        ranking_rows.append(
            [
                rank,
                str(row.get("name") or "—"),
                _ms(row.get("p95_ms")),
                _pct(row.get("error_rate")),
                str(row.get("score") if row.get("score") is not None else "—"),
                _fresh(row.get("freshness")),
                _verdict(row.get("verdict")),
            ]
        )
    lines = [
        "# RPCBench",
        "",
        f"{_esc(method)} · size {_esc(data.get('sample_budget'))} · "
        f"rank {_esc(data.get('rank_by'))} · similar {band_txt} · "
        f"sha={_esc(sha)}",
        "",
        f"**Fastest** {_esc(fastest_txt)} · "
        f"**Primary** {_esc(summary.get('primary') or 'none')} · "
        f"**Fallback** {_esc(summary.get('fallback') or 'none')}",
        "",
        _esc((data.get("route") or {}).get("why") or ""),
        "",
        "## Ranking",
        "",
        *_md_table(
            ["#", "name", "p95", "err", "rel", "fresh", "verdict"],
            ranking_rows,
            right=(True, False, True, True, True, False, False),
        ),
        "",
        "## Signals",
        "",
    ]
    cards = _signal_cards(data.get("ranking") or [])
    if not cards:
        lines.append("none")
    else:
        lines.extend(cards)
    utc = mark.get("utc") or "—"
    vantage = mark.get("vantage") or "—"
    version = mark.get("version") or data.get("version") or "—"
    lines.extend(
        [
            "",
            f"Cite `{_esc(version)}` sha={_esc(sha)} family={_esc(mark.get('family') or 'evm')} "
            f"vantage={_esc(vantage)} utc={_esc(utc)} · "
            f"[methodology]({DOCS_METHODOLOGY}) · [boundary]({DOCS_BOUNDARY})",
            "",
        ]
    )
    return "\n".join(lines)


def _md_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    right: tuple[bool, ...] | None = None,
) -> list[str]:
    """Padded GFM table so pipes line up in the file and on GitHub."""
    cols = len(headers)
    align = right or tuple(False for _ in headers)
    grid = [[_esc(h) for h in headers]]
    for row in rows:
        padded = [_esc(cell) for cell in row] + ["—"] * cols
        grid.append(padded[:cols])
    widths = [3] * cols
    for row in grid:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def cell(text: str, i: int) -> str:
        fill = widths[i] - len(text)
        return (" " * fill + text) if align[i] else (text + " " * fill)

    def pipe(row: list[str]) -> str:
        return "| " + " | ".join(cell(row[i], i) for i in range(cols)) + " |"

    rules: list[str] = []
    for i, width in enumerate(widths):
        dash = "-" * width
        rules.append((dash[:-1] + ":") if align[i] else dash)
    return [pipe(grid[0]), "| " + " | ".join(rules) + " |"] + [
        pipe(row) for row in grid[1:]
    ]


def _signal_cards(ranking: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in ranking:
        for sig in (row.get("verdict") or {}).get("signals") or []:
            name = row.get("name")
            sid = sig.get("id") or ""
            lines.append(f"**{_esc(name)} · {_esc(sid)}**")
            lines.append(f"- problem {_esc(sig.get('problem') or '')}")
            lines.append(f"- why {_esc(sig.get('why') or '')}")
            lines.append(f"- next {_esc(sig.get('next') or '')}")
            lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _verdict(raw: dict[str, Any] | None) -> str:
    if not raw:
        return "—"
    decision = _DECISION.get(str(raw.get("decision") or ""), str(raw.get("decision") or "—"))
    kind = raw.get("kind")
    if kind:
        return f"{decision} · {kind}"
    return decision


def _fresh(raw: dict[str, Any] | None) -> str:
    if not raw:
        return "—"
    verdict = raw.get("verdict")
    if verdict == "stale":
        return "stale"
    if verdict == "fresh":
        return "yes"
    return "—"


def _ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}ms"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{100 * value:.0f}%"


def _esc(value: Any) -> str:
    text = "—" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
