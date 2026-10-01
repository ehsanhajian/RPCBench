"""Merge JSON reports from multiple labeled vantages into one comparison."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rpcbench.diff import DiffError, load_report
from rpcbench.watermark import DOCS_METHODOLOGY, utc_stamp

SCHEMA_TOOL = "rpcbench"
MERGE_NOTE = (
    "Each input JSON is one vantage (one machine / region). "
    "Merged numbers are not a browser RUM and not a multi-hop path measure."
)


class MergeError(DiffError):
    """Incompatible or incomplete reports for a multi-vantage merge."""


@dataclass(frozen=True)
class RegionCell:
    vantage: str
    p95_ms: float | None
    error_rate: float | None
    rank: int | None


@dataclass(frozen=True)
class GlobalRow:
    name: str
    mean_p95_ms: float | None
    max_error_rate: float | None
    n_vantages: int
    cells: tuple[RegionCell, ...]


@dataclass(frozen=True)
class MergeResult:
    reports: tuple[dict[str, Any], ...]
    vantages: tuple[dict[str, Any], ...]
    rows: tuple[GlobalRow, ...]
    seed: int | None
    workload: str | None
    method: str | None
    family: str | None
    profile: str | None

    def as_dict(self) -> dict[str, Any]:
        matrix: dict[str, dict[str, Any]] = {}
        for row in self.rows:
            matrix[row.name] = {
                cell.vantage: {
                    "p95_ms": cell.p95_ms,
                    "error_rate": cell.error_rate,
                    "rank": cell.rank,
                }
                for cell in row.cells
            }
        return {
            "tool": SCHEMA_TOOL,
            "kind": "merge",
            "utc": utc_stamp(),
            "note": MERGE_NOTE,
            "methodology": DOCS_METHODOLOGY,
            "seed": self.seed,
            "workload": self.workload,
            "method": self.method,
            "profile": self.profile,
            "family": self.family,
            "vantages": list(self.vantages),
            "regions": matrix,
            "global": {
                "rank_by": "mean_p95",
                "ranking": [
                    {
                        "rank": i,
                        "name": row.name,
                        "mean_p95_ms": row.mean_p95_ms,
                        "max_error_rate": row.max_error_rate,
                        "n_vantages": row.n_vantages,
                    }
                    for i, row in enumerate(self.rows, start=1)
                ],
            },
        }


def merge_reports(reports: list[dict[str, Any]]) -> MergeResult:
    if len(reports) < 2:
        raise MergeError("merge needs at least two JSON reports")
    _assert_compatible(reports)
    labels = [_vantage_label(rep, index) for index, rep in enumerate(reports)]
    if len(set(labels)) != len(labels):
        raise MergeError(
            "duplicate vantage labels; set RPCBENCH_VANTAGE (or watermark.vantage) "
            "so each run is distinct"
        )
    vantages = tuple(_vantage_stamp(rep, label) for rep, label in zip(reports, labels))
    names = _provider_names(reports)
    rows: list[GlobalRow] = []
    for name in names:
        cells: list[RegionCell] = []
        p95s: list[float] = []
        errs: list[float] = []
        for rep, label in zip(reports, labels):
            entry = _ranking_entry(rep, name)
            p95 = _as_float(entry.get("p95_ms") if entry else None)
            err = _as_float(entry.get("error_rate") if entry else None)
            rank = entry.get("rank") if entry else None
            if isinstance(rank, bool) or not isinstance(rank, int):
                rank = None
            cells.append(
                RegionCell(vantage=label, p95_ms=p95, error_rate=err, rank=rank)
            )
            if p95 is not None:
                p95s.append(p95)
            if err is not None:
                errs.append(err)
        mean_p95 = (sum(p95s) / len(p95s)) if p95s else None
        rows.append(
            GlobalRow(
                name=name,
                mean_p95_ms=mean_p95,
                max_error_rate=max(errs) if errs else None,
                n_vantages=len(p95s),
                cells=tuple(cells),
            )
        )
    rows.sort(
        key=lambda row: (
            row.mean_p95_ms is None,
            row.mean_p95_ms if row.mean_p95_ms is not None else 0.0,
            row.name,
        )
    )
    first = reports[0]
    mark = first.get("watermark") or {}
    return MergeResult(
        reports=tuple(reports),
        vantages=vantages,
        rows=tuple(rows),
        seed=mark.get("seed", first.get("seed")),
        workload=mark.get("workload"),
        method=first.get("method"),
        family=mark.get("family") or first.get("family"),
        profile=first.get("profile"),
    )


def load_merge_inputs(paths: list[Path]) -> list[dict[str, Any]]:
    return [load_report(path) for path in paths]


def format_merge(result: MergeResult) -> str:
    lines = [
        f"Merge    {len(result.vantages)} vantages  ·  "
        f"seed={result.seed if result.seed is not None else '—'}  ·  "
        f"workload={result.workload or result.method or '—'}",
        f"Note     {MERGE_NOTE}",
        "",
        "Vantage",
    ]
    for stamp in result.vantages:
        extras = _stamp_extras(stamp)
        lines.append(
            f"  {stamp['label']:<16} {extras}".rstrip()
        )
    labels = [stamp["label"] for stamp in result.vantages]
    lines.extend(["", "Per region (P95 ms)"])
    header = f"{'provider':<16}" + "".join(f"{lab:>12}" for lab in labels)
    lines.append(header)
    for row in result.rows:
        cells = {cell.vantage: cell for cell in row.cells}
        line = f"{row.name:<16}"
        for lab in labels:
            cell = cells.get(lab)
            line += f"{_ms(cell.p95_ms if cell else None):>12}"
        lines.append(line)
    lines.extend(
        [
            "",
            "Global (mean P95 across vantages)",
            f"{'#':<4}{'provider':<16}{'p95':>10}{'err':>8}{'n':>4}",
        ]
    )
    for i, row in enumerate(result.rows, start=1):
        lines.append(
            f"{i:<4}{row.name:<16}{_ms(row.mean_p95_ms):>10}"
            f"{_err(row.max_error_rate):>8}{row.n_vantages:>4}"
        )
    lines.append("")
    return "\n".join(lines)


def format_merge_json(result: MergeResult) -> str:
    return json.dumps(result.as_dict(), indent=2, sort_keys=False) + "\n"


def _assert_compatible(reports: list[dict[str, Any]]) -> None:
    seeds = {_seed(rep) for rep in reports}
    if len(seeds) > 1:
        raise MergeError(
            f"seed mismatch across reports: {sorted(seeds)!r} "
            "(re-run with the same --seed)"
        )
    workloads = {_workload_key(rep) for rep in reports}
    if len(workloads) > 1:
        raise MergeError(
            f"workload/method mismatch across reports: {sorted(workloads)!r}"
        )
    families = {
        str((rep.get("watermark") or {}).get("family") or rep.get("family") or "evm")
        for rep in reports
    }
    if len(families) > 1:
        raise MergeError(f"family mismatch across reports: {sorted(families)!r}")


def _seed(rep: dict[str, Any]) -> object:
    mark = rep.get("watermark") or {}
    if "seed" in mark:
        return mark.get("seed")
    return rep.get("seed")


def _workload_key(rep: dict[str, Any]) -> str:
    mark = rep.get("watermark") or {}
    if mark.get("workload"):
        return str(mark["workload"])
    profile = rep.get("profile")
    if profile and profile != "single":
        return str(profile)
    return str(rep.get("method") or "unknown")


def _vantage_label(rep: dict[str, Any], index: int) -> str:
    mark = rep.get("watermark") or {}
    label = mark.get("vantage") or mark.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    meta = mark.get("vantage_meta")
    if isinstance(meta, dict) and meta.get("label"):
        return str(meta["label"])
    return f"vantage-{index + 1}"


def _vantage_stamp(rep: dict[str, Any], label: str) -> dict[str, Any]:
    mark = rep.get("watermark") or {}
    meta = mark.get("vantage_meta")
    if not isinstance(meta, dict):
        meta = {}
    return {
        "label": label,
        "region": meta.get("region") or mark.get("region"),
        "city": meta.get("city") or mark.get("city"),
        "asn": meta.get("asn") or mark.get("asn"),
        "hostname": meta.get("hostname") or mark.get("hostname"),
        "utc": mark.get("utc"),
        "git_sha": mark.get("git_sha"),
    }


def _stamp_extras(stamp: dict[str, Any]) -> str:
    bits: list[str] = []
    for key, prefix in (
        ("region", "region"),
        ("city", "city"),
        ("asn", "asn"),
        ("hostname", "host"),
        ("utc", "utc"),
    ):
        value = stamp.get(key)
        if value:
            bits.append(f"{prefix}={value}")
    return "  ".join(bits)


def _provider_names(reports: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for rep in reports:
        for entry in rep.get("ranking") or []:
            name = entry.get("name")
            if isinstance(name, str) and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _ranking_entry(rep: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in rep.get("ranking") or []:
        if entry.get("name") == name:
            return entry
    return {}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}"


def _err(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"
