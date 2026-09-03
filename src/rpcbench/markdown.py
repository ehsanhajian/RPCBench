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
    lines = [
        "# RPCBench",
        "",
        f"{_cell(method)} · size {_cell(data.get('sample_budget'))} · "
        f"rank {_cell(data.get('rank_by'))} · similar {band_txt} · "
        f"sha={_cell(sha)}",
        "",
        f"**Fastest** {_cell(fastest_txt)} · "
        f"**Primary** {_cell(summary.get('primary') or 'none')} · "
        f"**Fallback** {_cell(summary.get('fallback') or 'none')}",
        "",
        _cell((data.get("route") or {}).get("why") or ""),
        "",
        "## Ranking",
        "",
        "| # | name | p95 | err | fresh | verdict |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in data.get("ranking") or []:
        rank = "—" if row.get("rank") is None else str(row["rank"])
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(rank),
                    _cell(row.get("name")),
                    _cell(_ms(row.get("p95_ms"))),
                    _cell(_pct(row.get("error_rate"))),
                    _cell(_fresh(row.get("freshness"))),
                    _cell(_verdict(row.get("verdict"))),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Signals", ""])
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
            f"Cite `{_cell(version)}` sha={_cell(sha)} family={_cell(mark.get('family') or 'evm')} "
            f"vantage={_cell(vantage)} utc={_cell(utc)} · "
            f"[methodology]({DOCS_METHODOLOGY}) · [boundary]({DOCS_BOUNDARY})",
            "",
        ]
    )
    return "\n".join(lines)


def _signal_cards(ranking: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in ranking:
        for sig in (row.get("verdict") or {}).get("signals") or []:
            name = row.get("name")
            sid = sig.get("id") or ""
            lines.append(f"**{_cell(name)} · {_cell(sid)}**")
            lines.append(f"- problem {_cell(sig.get('problem') or '')}")
            lines.append(f"- why {_cell(sig.get('why') or '')}")
            lines.append(f"- next {_cell(sig.get('next') or '')}")
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


def _cell(value: Any) -> str:
    text = "—" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
