"""Compare two JSON reports. CI exit if the primary got worse beyond the similar-band."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rpcbench.report import DEFAULT_SIMILAR_BAND, values_similar
from rpcbench.watermark import utc_stamp

SCHEMA_TOOL = "rpcbench"


class DiffError(Exception):
    """Unreadable or incomplete report JSON."""


@dataclass(frozen=True)
class ProviderDelta:
    name: str
    old_p95_ms: float | None
    new_p95_ms: float | None
    old_rank_value: float | None
    new_rank_value: float | None
    old_verdict: str | None
    new_verdict: str | None

    @property
    def delta_ms(self) -> float | None:
        if self.old_p95_ms is None or self.new_p95_ms is None:
            return None
        return self.new_p95_ms - self.old_p95_ms


@dataclass(frozen=True)
class SignalDelta:
    name: str
    id: str
    problem: str


@dataclass(frozen=True)
class ReportDiff:
    old: dict[str, Any]
    new: dict[str, Any]
    rank_by: str
    similar_band: float
    rows: tuple[ProviderDelta, ...]
    new_signals: tuple[SignalDelta, ...]
    winner_changed: bool
    primary_changed: bool
    primary_worse: bool
    old_primary: str | None
    new_primary: str | None
    old_fastest: tuple[str, ...]
    new_fastest: tuple[str, ...]

    @property
    def failed(self) -> bool:
        """True when CI should exit 1: the previous primary got worse beyond the band."""
        return self.primary_worse


def load_report(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DiffError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DiffError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("tool") != SCHEMA_TOOL:
        raise DiffError(f"{path} is not an rpcbench JSON report")
    if "ranking" not in data or "summary" not in data:
        raise DiffError(f"{path} is missing ranking/summary")
    return data


def history_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise DiffError(f"history dir not found: {directory}")
    files = sorted(p for p in directory.glob("*.json") if p.is_file())
    if len(files) < 2:
        raise DiffError(f"{directory} needs at least two JSON reports")
    return files


def write_history(directory: Path, blob: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    data = json.loads(blob)
    mark = data.get("watermark") or {}
    utc = str(mark.get("utc") or utc_stamp()).replace(":", "-")
    seq = str(data.get("sequence_id") or "run")[:8]
    path = directory / f"{utc}_{seq}.json"
    if path.exists():
        path = directory / f"{utc}_{seq}_{len(list(directory.glob('*.json')))}.json"
    path.write_text(blob, encoding="utf-8")
    return path


def compare_reports(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    similar_band: float | None = None,
) -> ReportDiff:
    band = (
        DEFAULT_SIMILAR_BAND
        if similar_band is None
        else similar_band
    )
    if similar_band is None:
        raw = new.get("similar_band")
        if isinstance(raw, (int, float)):
            band = float(raw)
    rank_by = str(new.get("rank_by") or old.get("rank_by") or "p95")
    higher = rank_by == "rps"
    old_by = {row["name"]: row for row in old.get("ranking") or [] if row.get("name")}
    new_by = {row["name"]: row for row in new.get("ranking") or [] if row.get("name")}
    names: list[str] = []
    for row in new.get("ranking") or []:
        name = row.get("name")
        if name and name not in names:
            names.append(name)
    for row in old.get("ranking") or []:
        name = row.get("name")
        if name and name not in names:
            names.append(name)
    rows = tuple(_delta(name, old_by.get(name), new_by.get(name)) for name in names)
    old_summary = old.get("summary") or {}
    new_summary = new.get("summary") or {}
    old_primary = old_summary.get("primary")
    new_primary = new_summary.get("primary")
    old_fastest = tuple(old_summary.get("fastest_names") or [])
    new_fastest = tuple(new_summary.get("fastest_names") or [])
    primary_worse = _primary_worse(
        old_primary,
        old_by,
        new_by,
        band=band,
        higher_is_better=higher,
    )
    return ReportDiff(
        old=old,
        new=new,
        rank_by=rank_by,
        similar_band=band,
        rows=rows,
        new_signals=_new_signals(old_by, new_by),
        winner_changed=old_fastest != new_fastest,
        primary_changed=old_primary != new_primary,
        primary_worse=primary_worse,
        old_primary=old_primary,
        new_primary=new_primary,
        old_fastest=old_fastest,
        new_fastest=new_fastest,
    )


def format_diff(diff: ReportDiff) -> str:
    """Compact, CI-friendly text. Not a scanner card."""
    status = "FAIL" if diff.failed else "ok"
    band_pct = f"{100 * diff.similar_band:.0f}%"
    old_mark = diff.old.get("watermark") or {}
    new_mark = diff.new.get("watermark") or {}
    lines = [
        "RPCBench diff",
        f"result    {status}",
        f"old       {_stamp(old_mark)}",
        f"new       {_stamp(new_mark)}",
        f"rank      {diff.rank_by}  ·  similar {band_pct}",
        "",
        f"Primary   {_primary_line(diff)}",
        f"Winner    {_join(diff.old_fastest) or 'none'} → {_join(diff.new_fastest) or 'none'}"
        + ("  changed" if diff.winner_changed else ""),
        f"Signals   {_signals_line(diff)}",
        "",
        f"{'#':<2}  {'name':<16}  {'old':>8}  {'new':>8}  {'Δ':>8}",
    ]
    for i, row in enumerate(diff.rows, start=1):
        mark = str(i)
        lines.append(
            f"{mark:<2}  {row.name:<16}  {_ms(row.old_p95_ms):>8}  "
            f"{_ms(row.new_p95_ms):>8}  {_delta_cell(row):>8}"
        )
    if diff.failed:
        who = diff.old_primary or "primary"
        lines.append("")
        lines.append(
            f"Primary {who} got worse beyond the {band_pct} similar-band."
        )
    lines.append("")
    return "\n".join(lines)


def format_diff_md(diff: ReportDiff) -> str:
    status = "FAIL" if diff.failed else "ok"
    band_pct = f"{100 * diff.similar_band:.0f}%"
    lines = [
        "# RPCBench diff",
        "",
        f"**result** {status} · rank `{_cell(diff.rank_by)}` · similar {band_pct}",
        "",
        f"**Primary** {_cell(_primary_line(diff))}",
        f"**Winner** {_cell(_join(diff.old_fastest) or 'none')} → "
        f"{_cell(_join(diff.new_fastest) or 'none')}"
        + (" · changed" if diff.winner_changed else ""),
        f"**Signals** {_cell(_signals_line(diff))}",
        "",
        "| # | name | old | new | Δ |",
        "| --- | --- | --- | --- | --- |",
    ]
    for i, row in enumerate(diff.rows, start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    _cell(row.name),
                    _cell(_ms(row.old_p95_ms)),
                    _cell(_ms(row.new_p95_ms)),
                    _cell(_delta_cell(row)),
                ]
            )
            + " |"
        )
    if diff.failed:
        who = diff.old_primary or "primary"
        lines.extend(
            [
                "",
                f"Primary `{_cell(who)}` got worse beyond the {band_pct} similar-band.",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _delta(
    name: str, old: dict[str, Any] | None, new: dict[str, Any] | None
) -> ProviderDelta:
    return ProviderDelta(
        name=name,
        old_p95_ms=_num((old or {}).get("p95_ms")),
        new_p95_ms=_num((new or {}).get("p95_ms")),
        old_rank_value=_num((old or {}).get("rank_value")),
        new_rank_value=_num((new or {}).get("rank_value")),
        old_verdict=_kind(old),
        new_verdict=_kind(new),
    )


def _primary_worse(
    old_primary: str | None,
    old_by: dict[str, dict[str, Any]],
    new_by: dict[str, dict[str, Any]],
    *,
    band: float,
    higher_is_better: bool,
) -> bool:
    if not old_primary:
        return False
    old_row = old_by.get(old_primary)
    new_row = new_by.get(old_primary)
    old_val = _num((old_row or {}).get("rank_value"))
    new_val = _num((new_row or {}).get("rank_value"))
    if new_row is None or new_val is None:
        return old_val is not None
    if old_val is None:
        return False
    if values_similar(old_val, new_val, band, higher_is_better=higher_is_better):
        return False
    if higher_is_better:
        return new_val < old_val
    return new_val > old_val


def _new_signals(
    old_by: dict[str, dict[str, Any]],
    new_by: dict[str, dict[str, Any]],
) -> tuple[SignalDelta, ...]:
    seen = {
        (name, str(sig.get("id") or ""))
        for name, row in old_by.items()
        for sig in (row.get("verdict") or {}).get("signals") or []
    }
    out: list[SignalDelta] = []
    for name, row in new_by.items():
        for sig in (row.get("verdict") or {}).get("signals") or []:
            sid = str(sig.get("id") or "")
            if (name, sid) in seen:
                continue
            out.append(
                SignalDelta(
                    name=name,
                    id=sid,
                    problem=str(sig.get("problem") or ""),
                )
            )
    return tuple(out)


def _primary_line(diff: ReportDiff) -> str:
    old_p = diff.old_primary or "none"
    new_p = diff.new_primary or "none"
    extra = "  changed" if diff.primary_changed else ""
    row = next((item for item in diff.rows if item.name == diff.old_primary), None)
    if row is None:
        return f"{old_p} → {new_p}{extra}"
    delta = _delta_cell(row)
    return (
        f"{old_p}  {_ms(row.old_p95_ms)} → {_ms(row.new_p95_ms)}  ({delta}){extra}"
    )


def _signals_line(diff: ReportDiff) -> str:
    if not diff.new_signals:
        return "none new"
    return "new " + ", ".join(
        f"{row.name} {row.id}".strip() for row in diff.new_signals
    )


def _delta_cell(row: ProviderDelta) -> str:
    if row.delta_ms is None:
        return "—"
    sign = "+" if row.delta_ms > 0 else ""
    return f"{sign}{row.delta_ms:.1f}ms"


def _stamp(mark: dict[str, Any]) -> str:
    sha = mark.get("git_sha") or "—"
    utc = mark.get("utc") or "—"
    vantage = mark.get("vantage") or "—"
    return f"sha={sha}  utc={utc}  vantage={vantage}"


def _join(names: tuple[str, ...]) -> str:
    return ", ".join(names)


def _kind(row: dict[str, Any] | None) -> str | None:
    if not row:
        return None
    verd = row.get("verdict") or {}
    return verd.get("kind")


def _ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}ms"


def _cell(value: Any) -> str:
    text = "—" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
