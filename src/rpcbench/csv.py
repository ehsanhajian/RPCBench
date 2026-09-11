"""Flat CSV compare report. One row per provider. Not a sample dump."""

from __future__ import annotations

import csv
import io
from typing import Any

from rpcbench.logs import LOGS_RANGES
from rpcbench.methods import is_app_workload
from rpcbench.report import (
    DEFAULT_RANK_BY,
    DEFAULT_SIMILAR_BAND,
    batch_support_label,
    run_to_dict,
)
from rpcbench.run import RunResult

COLUMNS = (
    "utc",
    "vantage",
    "workload",
    "rank_by",
    "rank",
    "name",
    "ok",
    "responded",
    "primary",
    "n_ok",
    "n_fail",
    "error_rate",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "mean_ms",
    "jitter_ms",
    "rps",
    "score",
    "verdict",
    "kind",
    "fresh",
    "match",
    "http_version",
    "encoding",
    "bytes_in_p95",
    "batch_supported",
    "batch_ms",
    "serial_ms",
    "batch_ratio",
    "logs_1_ms",
    "logs_1_bytes",
    "logs_1_n",
    "logs_1_status",
    "logs_1_trunc",
    "logs_10_ms",
    "logs_10_bytes",
    "logs_10_n",
    "logs_10_status",
    "logs_10_trunc",
    "logs_100_ms",
    "logs_100_bytes",
    "logs_100_n",
    "logs_100_status",
    "logs_100_trunc",
    "logs_1000_ms",
    "logs_1000_bytes",
    "logs_1000_n",
    "logs_1000_status",
    "logs_1000_trunc",
)


def format_csv(
    result: RunResult,
    *,
    rank_by: str = DEFAULT_RANK_BY,
    similar_band: float = DEFAULT_SIMILAR_BAND,
) -> str:
    """Spreadsheet CSV. Same numbers as JSON ranking. No per-sample rows."""
    return format_csv_dict(
        run_to_dict(result, rank_by=rank_by, similar_band=similar_band)
    )


def format_csv_dict(data: dict[str, Any]) -> str:
    mark = data.get("watermark") or {}
    summary = data.get("summary") or {}
    primary = summary.get("primary")
    workload = mark.get("workload") or data.get("method") or ""
    if is_app_workload(data.get("profile")):
        workload = str(data.get("profile") or workload)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=COLUMNS,
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in data.get("ranking") or []:
        writer.writerow(
            {
                "utc": mark.get("utc") or "",
                "vantage": mark.get("vantage") or "",
                "workload": workload,
                "rank_by": data.get("rank_by") or "",
                "rank": "" if row.get("rank") is None else row["rank"],
                "name": row.get("name") or "",
                "ok": _bool(row.get("ok")),
                "responded": _bool(row.get("ok")),
                "primary": _bool(row.get("name") == primary and primary),
                "n_ok": _num(row.get("n_ok")),
                "n_fail": _num(row.get("n_fail")),
                "error_rate": _num(row.get("error_rate")),
                "p50_ms": _num(row.get("p50_ms")),
                "p95_ms": _num(row.get("p95_ms")),
                "p99_ms": _num(row.get("p99_ms")),
                "mean_ms": _num(row.get("mean_ms")),
                "jitter_ms": _num(row.get("jitter_ms")),
                "rps": _num(row.get("rps")),
                "score": _num(row.get("score")),
                "verdict": (row.get("verdict") or {}).get("decision") or "",
                "kind": (row.get("verdict") or {}).get("kind") or "",
                "fresh": _fresh(row.get("freshness")),
                "match": _match(row.get("consistency")),
                "http_version": (row.get("transport") or {}).get("http_version") or "",
                "encoding": (row.get("transport") or {}).get("encoding") or "",
                "bytes_in_p95": _num((row.get("transport") or {}).get("bytes_in_p95")),
                "batch_supported": _batch_supported(row.get("batch")),
                "batch_ms": _num((row.get("batch") or {}).get("batch_ms")),
                "serial_ms": _num((row.get("batch") or {}).get("serial_ms")),
                "batch_ratio": _num((row.get("batch") or {}).get("ratio")),
                **_logs_range_cols(row.get("logs_range")),
            }
        )
    return buf.getvalue()


def _fresh(raw: dict[str, Any] | None) -> str:
    if not raw:
        return ""
    verdict = raw.get("verdict")
    if verdict == "stale":
        return "stale"
    if verdict == "fresh":
        return "yes"
    return ""


def _match(raw: dict[str, Any] | None) -> str:
    if not raw:
        return ""
    verdict = raw.get("verdict")
    if verdict == "agree":
        return "yes"
    if verdict == "disagree":
        return "no"
    return ""


def _batch_supported(raw: dict[str, Any] | None) -> str:
    if not raw:
        return ""
    label = batch_support_label(raw)
    if label == "yes":
        return "true"
    if label in {"partial", "skip"}:
        return label
    return "false"


def _logs_range_cols(rows: Any) -> dict[str, str]:
    by_blocks = {}
    if isinstance(rows, list):
        for hit in rows:
            if isinstance(hit, dict) and hit.get("blocks") is not None:
                by_blocks[int(hit["blocks"])] = hit
    cols: dict[str, str] = {}
    for span in LOGS_RANGES:
        hit = by_blocks.get(span) or {}
        cols[f"logs_{span}_ms"] = _num(hit.get("latency_ms"))
        cols[f"logs_{span}_bytes"] = _num(hit.get("bytes_in"))
        cols[f"logs_{span}_n"] = _num(hit.get("n_logs"))
        cols[f"logs_{span}_status"] = str(hit.get("status") or "")
        trunc = hit.get("truncated")
        if hit:
            cols[f"logs_{span}_trunc"] = _bool(trunc)
        else:
            cols[f"logs_{span}_trunc"] = ""
    return cols


def _bool(value: Any) -> str:
    return "true" if value else "false"


def _num(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return _bool(value)
    if isinstance(value, int):
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.6g}"
