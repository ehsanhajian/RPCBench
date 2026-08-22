"""Human-readable CLI report and machine-readable JSON. No security findings."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any

from rpcbench import __version__
from rpcbench.run import EndpointOutcome, HISTOGRAM_EDGES_MS, HISTOGRAM_LABELS, RunResult

SCHEMA_VERSION = 1

DEFAULT_RANK_BY = "p95"
DEFAULT_SIMILAR_BAND = 0.10
P99_MIN_N = 100
RANK_BY_KEYS = ("p50", "p95", "p99", "mean", "rps")
RANK_BY_ALIASES = {"throughput": "rps"}
_RANK_LABELS = {
    "p50": "p50",
    "p95": "p95",
    "p99": "p99",
    "mean": "mean",
    "rps": "rps",
}

_GREEN = "32"
_RED = "31"
_BOLD = "1"


class RankError(ValueError):
    pass


def normalize_rank_by(raw: str) -> str:
    key = raw.strip().lower()
    key = RANK_BY_ALIASES.get(key, key)
    if key not in RANK_BY_KEYS:
        known = ", ".join((*RANK_BY_KEYS, "throughput"))
        raise RankError(f"unknown --rank-by {raw!r} (try {known})")
    return key


def normalize_similar_band(raw: float) -> float:
    if raw < 0 or raw > 1:
        raise RankError("--similar-band must be between 0 and 1 (default 0.10 = 10%)")
    return raw


def p99_reliable(n_ok: int) -> bool:
    """Nearest-rank P99 is the max until n ≥ 100."""
    return n_ok >= P99_MIN_N


def values_similar(
    left: float, right: float, band: float, *, higher_is_better: bool
) -> bool:
    if left == right:
        return True
    lo, hi = (left, right) if left <= right else (right, left)
    better = hi if higher_is_better else lo
    if better == 0:
        return hi == 0
    return (hi - lo) / better <= band


def reliable_for_place(stats: Any, similar_band: float) -> bool:
    if stats.n_ok == 0:
        return False
    if stats.error_rate is not None and stats.error_rate > similar_band:
        return False
    return True


@dataclass(frozen=True)
class RankedPlace:
    outcome: EndpointOutcome
    rank: int | None
    similar: bool
    reliable: bool
    p99_reliable: bool


def rank_outcomes(
    result: RunResult,
    *,
    rank_by: str = DEFAULT_RANK_BY,
    similar_band: float = DEFAULT_SIMILAR_BAND,
) -> tuple[EndpointOutcome, ...]:
    """Reliable by rank key, then high-error rows, then failures. Config order on ties."""
    return tuple(
        row.outcome
        for row in place_outcomes(
            result, rank_by=rank_by, similar_band=similar_band
        )
    )


def place_outcomes(
    result: RunResult,
    *,
    rank_by: str = DEFAULT_RANK_BY,
    similar_band: float = DEFAULT_SIMILAR_BAND,
) -> tuple[RankedPlace, ...]:
    key_name = normalize_rank_by(rank_by)
    band = normalize_similar_band(similar_band)
    higher = key_name == "rps"

    def sort_key(item: tuple[int, EndpointOutcome]) -> tuple[int, float, float, int]:
        index, outcome = item
        stats = outcome.stats
        value = _rank_value(stats, key_name)
        if value is None:
            return (2, 0.0, 0.0, index)
        tier = 0 if reliable_for_place(stats, band) else 1
        mean = stats.mean_ms if stats.mean_ms is not None else 0.0
        if higher:
            return (tier, -value, mean, index)
        return (tier, value, mean, index)

    ordered = [outcome for _, outcome in sorted(enumerate(result.outcomes), key=sort_key)]
    rows: list[RankedPlace] = []
    next_place = 1
    leader_val: float | None = None
    leader_place: int | None = None
    for outcome in ordered:
        stats = outcome.stats
        value = _rank_value(stats, key_name)
        p99_ok = p99_reliable(stats.n_ok)
        if value is None:
            rows.append(RankedPlace(outcome, None, False, False, p99_ok))
            continue
        if not reliable_for_place(stats, band):
            rows.append(RankedPlace(outcome, None, False, False, p99_ok))
            continue
        if leader_val is not None and values_similar(
            leader_val, value, band, higher_is_better=higher
        ):
            rows.append(RankedPlace(outcome, leader_place, True, True, p99_ok))
            continue
        leader_val = value
        leader_place = next_place
        next_place += 1
        rows.append(RankedPlace(outcome, leader_place, False, True, p99_ok))
    counts = Counter(row.rank for row in rows if row.rank is not None)
    return tuple(
        RankedPlace(
            row.outcome,
            row.rank,
            counts.get(row.rank, 0) > 1,
            row.reliable,
            row.p99_reliable,
        )
        if row.rank is not None
        else row
        for row in rows
    )


def color_enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    if os.environ.get("NO_COLOR", "").strip():
        return False
    if os.environ.get("FORCE_COLOR", "").strip():
        return True
    return bool(sys.stdout.isatty())


def _paint(text: str, *codes: str, enabled: bool) -> str:
    if not enabled or not codes:
        return text
    prefix = ";".join(codes)
    return f"\033[{prefix}m{text}\033[0m"


def _rank_value(stats: Any, rank_by: str) -> float | None:
    if stats.n_ok == 0:
        return None
    if rank_by == "p50":
        return stats.p50_ms
    if rank_by == "p95":
        return stats.p95_ms
    if rank_by == "p99":
        return stats.p99_ms
    if rank_by == "mean":
        return stats.mean_ms
    if rank_by == "rps":
        if not stats.mean_ms:
            return None
        return 1000.0 / stats.mean_ms
    return None


def run_to_dict(
    result: RunResult,
    *,
    rank_by: str = DEFAULT_RANK_BY,
    similar_band: float = DEFAULT_SIMILAR_BAND,
) -> dict[str, Any]:
    """Stable JSON object. Redacted URL + hash only; complete enough to rebuild the CLI summary."""
    rank_by = normalize_rank_by(rank_by)
    band = normalize_similar_band(similar_band)
    placed = place_outcomes(result, rank_by=rank_by, similar_band=band)
    ranked = tuple(row.outcome for row in placed)
    ok_rows = [o for o in ranked if o.stats.n_ok]
    fail_rows = [o for o in ranked if not o.stats.n_ok]
    winners = [row for row in placed if row.rank == 1]
    ranking: list[dict[str, Any]] = []
    providers: list[dict[str, Any]] = []
    for row in placed:
        ranking.append(_ranking_entry(row, rank_by))
        providers.append(_provider_entry(row, result.method))
    return {
        "tool": "rpcbench",
        "version": __version__,
        "schema": SCHEMA_VERSION,
        "method": result.method,
        "params": list(result.params),
        "profile": result.profile,
        "workload": [
            {
                "name": spec.name,
                "method": spec.method,
                "params": list(spec.params),
            }
            for spec in result.workload
        ],
        "samples": result.samples,
        "warmup": result.warmup,
        "sample_budget": result.sample_budget,
        "timeout": result.timeout,
        "budget": result.budget,
        "budget_remaining": result.budget_remaining,
        "concurrency": result.concurrency,
        "mode": result.mode,
        "seed": result.seed,
        "sequence_id": result.sequence_id,
        "rank_by": rank_by,
        "similar_band": band,
        "histogram_buckets": _histogram_bucket_defs(),
        "summary": {
            "fastest": winners[0].outcome.endpoint.name if len(winners) == 1 else None,
            "fastest_names": [row.outcome.endpoint.name for row in winners],
            "fastest_similar": len(winners) > 1,
            "ok": len(ok_rows),
            "failed": len(fail_rows),
            "failed_names": [o.endpoint.name for o in fail_rows],
        },
        "comparison": [
            _comparison_entry(outcome, result.method) for outcome in result.outcomes
        ],
        "ranking": ranking,
        "methods": _methods_json(result),
        "providers": providers,
        "capabilities": {
            "method": result.method,
            "responded": len(ok_rows),
            "total": len(ranked),
            "missed": [
                {"name": o.endpoint.name, "error_class": _miss_class(o)}
                for o in fail_rows
            ],
        },
        "pairs": [
            {
                "index": pair.index,
                "kind": pair.kind,
                "method": pair.method,
                "bodies": dict(pair.bodies),
            }
            for pair in result.pairs
        ],
    }


def format_json(
    result: RunResult,
    *,
    rank_by: str = DEFAULT_RANK_BY,
    similar_band: float = DEFAULT_SIMILAR_BAND,
) -> str:
    return (
        json.dumps(
            run_to_dict(result, rank_by=rank_by, similar_band=similar_band),
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )


def format_run(
    result: RunResult,
    *,
    verbose: bool = False,
    color: bool | None = None,
    rank_by: str = DEFAULT_RANK_BY,
    similar_band: float = DEFAULT_SIMILAR_BAND,
) -> str:
    rank_by = normalize_rank_by(rank_by)
    band = normalize_similar_band(similar_band)
    use_color = color_enabled(color)
    placed = place_outcomes(result, rank_by=rank_by, similar_band=band)
    ranked = tuple(row.outcome for row in placed)
    ok_rows = [o for o in ranked if o.stats.n_ok]
    fail_rows = [o for o in ranked if not o.stats.n_ok]
    params = f" {list(result.params)}" if result.params else ""
    label = _RANK_LABELS[rank_by]
    band_pct = f"{100 * band:.0f}%"
    method_line = _method_header(result, params)
    compare_what = "mix" if result.profile == "mix" else result.method
    lines = [
        "RPCBench",
        "=" * 72,
        method_line,
        f"Samples   {result.samples} after {result.warmup} warmup  ·  "
        f"size {result.sample_budget}  ·  "
        f"Timeout {result.timeout:g}s  ·  "
        f"requests {result.budget} ({result.budget_remaining} left)  ·  "
        f"Rank by {label}  ·  similar {band_pct}",
        f"Mode      {result.mode}  ·  seed={result.seed}  ·  "
        f"seq={result.sequence_id or '—'}  ·  "
        f"concurrency={_concurrency_label(result.concurrency)}",
        "",
        "Summary",
    ]
    lines.extend(
        _summary_lines(placed, fail_rows, len(result.outcomes), use_color, rank_by, band)
    )
    name_w = max((len(o.endpoint.name) for o in result.outcomes), default=4)
    lines.extend(
        [
            "",
            f"Comparison  (config order · {result.mode} · same {compare_what}, samples, and budget)",
        ]
    )
    lines.extend(_comparison_lines(result, name_w, use_color))
    lines.extend(
        ["", f"Ranking  (by {label}; similar within {band_pct}; ~ high err; failed last)"]
    )
    for row in placed:
        if row.rank is not None:
            mark = f"{row.rank:>3}"
        elif row.outcome.stats.n_ok:
            mark = "  ~"
        else:
            mark = "  —"
        lines.append(_ranking_line(row.outcome, mark, name_w, use_color, rank_by))
    if result.profile == "mix" or len(result.workload) > 1:
        lines.extend(["", "Methods  (per-method; ranking uses the whole mix)"])
        lines.extend(_methods_lines(result, name_w, use_color))
    lines.extend(["", "Providers"])
    for outcome in ranked:
        lines.extend(_provider_lines(outcome, name_w, verbose, use_color))
    lines.extend(["", "Capabilities"])
    lines.extend(_capability_lines(result, ranked))
    lines.append("")
    extra_p99 = ""
    if any(not row.p99_reliable and row.outcome.stats.n_ok for row in placed):
        extra_p99 = f"  ·  P99 is the slowest sample until n≥{P99_MIN_N}"
    lines.append(
        f"{len(ok_rows)} ok  {len(fail_rows)} failed  ·  warmup excluded  ·  "
        "err=failed/attempted  ·  min/mean/max, jitter (stddev), p50/p95/p99, "
        f"and histogram of successful samples  ·  similar-band {band_pct}"
        f"{extra_p99}"
    )
    return "\n".join(lines) + "\n"


def _summary_lines(
    placed: tuple[RankedPlace, ...] | list[RankedPlace],
    fail_rows: list[EndpointOutcome],
    total: int,
    use_color: bool,
    rank_by: str,
    similar_band: float,
) -> list[str]:
    lines: list[str] = []
    winners = [row for row in placed if row.rank == 1]
    if winners:
        names = ", ".join(
            _paint(row.outcome.endpoint.name, _BOLD, _GREEN, enabled=use_color)
            for row in winners
        )
        stats = winners[0].outcome.stats
        bits = [_rank_metric_text(stats, rank_by)]
        if rank_by != "mean":
            bits.append(f"mean={stats.mean_ms:.1f}ms")
        if rank_by != "p95":
            bits.append(f"p95={stats.p95_ms:.1f}ms")
        bits.append(f"err={_pct(stats.error_rate)}")
        extra = ""
        if len(winners) > 1:
            extra = (
                f"  (similar within {100 * similar_band:.0f}% {_RANK_LABELS[rank_by]})"
            )
        lines.append(f"  Fastest  {names}  " + "  ".join(bits) + extra)
    elif any(row.outcome.stats.n_ok for row in placed):
        lines.append("  Fastest  none  (no reliable place)")
    else:
        lines.append("  Fastest  none  (all endpoints failed)")
    if fail_rows:
        names = ", ".join(o.endpoint.name for o in fail_rows)
        lines.append(f"  Failed   {len(fail_rows)}/{total}    {names}")
    else:
        lines.append(f"  Failed   0/{total}")
    return lines


def _method_header(result: RunResult, params: str) -> str:
    if result.profile == "mix" and result.workload:
        steps = ", ".join(spec.name for spec in result.workload)
        return f"Method    mix  ·  {steps}"
    return f"Method    {result.method}{params}"


def _methods_lines(
    result: RunResult, name_w: int, use_color: bool
) -> list[str]:
    step_w = max((len(spec.name) for spec in result.workload), default=4)
    method_w = max((len(spec.method) for spec in result.workload), default=6)
    header = (
        f"  {'endpoint':<{name_w}}  {'step':<{step_w}}  {'method':<{method_w}}  "
        f"{'n':>7}  {'err':>4}  {'p50':>8}  {'p95':>8}  {'p99':>8}"
    )
    lines = [header]
    lookup = {spec.name: spec.method for spec in result.workload}
    for outcome in result.outcomes:
        hue = _GREEN if outcome.stats.n_ok else _RED
        name = _paint(f"{outcome.endpoint.name:<{name_w}}", hue, enabled=use_color)
        for step, stats in outcome.by_method:
            attempted = stats.n_ok + stats.n_fail
            n = f"{stats.n_ok}/{attempted}" if attempted else "—"
            p99 = _cell_ms(stats.p99_ms)
            lines.append(
                f"  {name}  {step:<{step_w}}  {lookup.get(step, step):<{method_w}}  "
                f"{n:>7}  {_pct(stats.error_rate):>4}  "
                f"{_cell_ms(stats.p50_ms):>8}  {_cell_ms(stats.p95_ms):>8}  "
                f"{p99:>8}"
            )
    return lines


def _methods_json(result: RunResult) -> list[dict[str, Any]]:
    lookup = {spec.name: spec.method for spec in result.workload}
    rows: list[dict[str, Any]] = []
    for outcome in result.outcomes:
        for step, stats in outcome.by_method:
            rows.append(
                {
                    "name": outcome.endpoint.name,
                    "step": step,
                    "method": lookup.get(step, step),
                    "n_ok": stats.n_ok,
                    "n_fail": stats.n_fail,
                    "error_rate": stats.error_rate,
                    "p50_ms": stats.p50_ms,
                    "p95_ms": stats.p95_ms,
                    "p99_ms": stats.p99_ms,
                    "p99_reliable": p99_reliable(stats.n_ok),
                    "mean_ms": stats.mean_ms,
                    "jitter_ms": stats.jitter_ms,
                }
            )
    return rows


def _comparison_lines(
    result: RunResult, name_w: int, use_color: bool
) -> list[str]:
    header = (
        f"  {'name':<{name_w}}  status  {'n':>7}  {'err':>4}  "
        f"{'p50':>8}  {'p95':>8}  {'p99':>8}  {'jit':>8}  {'rps':>6}  cap"
    )
    lines = [header]
    for outcome in result.outcomes:
        lines.append(_comparison_line(outcome, name_w, use_color))
    return lines


def _comparison_line(outcome: EndpointOutcome, name_w: int, use_color: bool) -> str:
    stats = outcome.stats
    ok = stats.n_ok > 0
    raw_status = f"{'ok' if ok else 'fail':<6}"
    status = _paint(raw_status, _GREEN if ok else _RED, enabled=use_color)
    attempted = stats.n_ok + stats.n_fail
    n = f"{stats.n_ok}/{attempted}"
    name = _paint(
        f"{outcome.endpoint.name:<{name_w}}",
        _GREEN if ok else _RED,
        enabled=use_color,
    )
    cap = "yes" if ok else _miss_class(outcome)
    return (
        f"  {name}  {status}  {n:>7}  {_pct(stats.error_rate):>4}  "
        f"{_cell_ms(stats.p50_ms)}  {_cell_ms(stats.p95_ms)}  "
        f"{_cell_ms(stats.p99_ms)}  {_cell_ms(stats.jitter_ms)}  "
        f"{_cell_rps(stats.mean_ms)}  {cap}"
    )


def _cell_ms(value: float | None) -> str:
    if value is None:
        return f"{'—':>8}"
    return f"{value:6.1f}ms"


def _cell_rps(mean_ms: float | None) -> str:
    if not mean_ms:
        return f"{'—':>6}"
    return f"{1000.0 / mean_ms:>6.1f}"


def _comparison_entry(outcome: EndpointOutcome, method: str) -> dict[str, Any]:
    stats = outcome.stats
    rps = (1000.0 / stats.mean_ms) if stats.mean_ms else None
    responded = stats.n_ok > 0
    return {
        "name": outcome.endpoint.name,
        "ok": responded,
        "n_ok": stats.n_ok,
        "n_fail": stats.n_fail,
        "error_rate": stats.error_rate,
        "mean_ms": stats.mean_ms,
        "p50_ms": stats.p50_ms,
        "p95_ms": stats.p95_ms,
        "p99_ms": stats.p99_ms,
        "p99_reliable": p99_reliable(stats.n_ok),
        "jitter_ms": stats.jitter_ms,
        "histogram": _histogram_json(stats.histogram),
        "rps": rps,
        "capability": {
            "method": method,
            "responded": responded,
            "error_class": None if responded else _miss_class(outcome),
        },
        "last_error": _last_error(outcome) or None,
    }


def _rank_metric_text(stats: Any, rank_by: str) -> str:
    value = _rank_value(stats, rank_by)
    if value is None:
        return f"{_RANK_LABELS[rank_by]}=n/a"
    if rank_by == "rps":
        return f"rps={value:.1f}"
    return f"{_RANK_LABELS[rank_by]}={value:.1f}ms"


def _ranking_line(
    outcome: EndpointOutcome,
    mark: str,
    name_w: int,
    use_color: bool,
    rank_by: str,
) -> str:
    stats = outcome.stats
    ok = stats.n_ok > 0
    status = _paint("ok" if ok else "fail", _GREEN if ok else _RED, enabled=use_color)
    attempted = stats.n_ok + stats.n_fail
    rate = f"err={_pct(stats.error_rate)}"
    classes = "".join(f"  {name}={count}" for name, count in stats.by_class)
    name = _paint(
        f"{outcome.endpoint.name:<{name_w}}",
        _GREEN if ok else _RED,
        enabled=use_color,
    )
    if ok:
        extra_mean = ""
        if rank_by != "mean":
            extra_mean = f"  mean={stats.mean_ms:.1f}ms"
        extra_p95 = ""
        if rank_by != "p95":
            extra_p95 = f"  p95={stats.p95_ms:.1f}ms"
        return (
            f"  {mark}  {name}  {status}  n={stats.n_ok}/{attempted}  {rate}"
            f"{classes}  {_rank_metric_text(stats, rank_by)}{extra_mean}{extra_p95}"
            f"  jitter={_jitter_text(stats.jitter_ms)}"
        )
    err = _last_error(outcome)
    extra = f"  {err}" if err else ""
    return (
        f"  {mark}  {name}  {status}  n={stats.n_ok}/{attempted}  {rate}"
        f"{classes}{extra}"
    )


def _provider_lines(
    outcome: EndpointOutcome, name_w: int, verbose: bool, use_color: bool
) -> list[str]:
    stats = outcome.stats
    ok = stats.n_ok > 0
    hue = _GREEN if ok else _RED
    status = _paint("ok" if ok else "fail", hue, enabled=use_color)
    url = outcome.endpoint.display_url
    url_id = outcome.endpoint.url_id
    indent = " " * (2 + name_w + 4)
    name = _paint(f"{outcome.endpoint.name:<{name_w}}", hue, enabled=use_color)
    lines = [f"  {name}  {status}  {url}  id={url_id}"]
    attempted = stats.n_ok + stats.n_fail
    rate = f"err={_pct(stats.error_rate)}"
    classes = "".join(f"  {name}={count}" for name, count in stats.by_class)
    lines.append(f"{indent}n={stats.n_ok}/{attempted}  {rate}{classes}")
    if stats.min_ms is not None:
        lines.append(
            f"{indent}min={stats.min_ms:.1f}ms  "
            f"mean={stats.mean_ms:.1f}ms  max={stats.max_ms:.1f}ms  "
            f"jitter={_jitter_text(stats.jitter_ms)}"
        )
        p99 = f"p99={stats.p99_ms:.1f}ms  (n={stats.n_ok})"
        if not p99_reliable(stats.n_ok):
            p99 += f"; need ≥{P99_MIN_N}"
        lines.append(
            f"{indent}p50={stats.p50_ms:.1f}ms  p95={stats.p95_ms:.1f}ms  {p99}"
        )
        lines.append(f"{indent}hist  {_histogram_text(stats.histogram)}")
    else:
        err = _last_error(outcome)
        if err:
            lines.append(f"{indent}{err}")
    if verbose:
        if outcome.warmup:
            lines.append(f"{indent}warmup")
            lines.extend(_sample_lines(outcome.warmup, indent))
        lines.append(f"{indent}samples")
        lines.extend(_sample_lines(outcome.samples, indent))
    return lines + [""]


def _sample_lines(hits: tuple, indent: str) -> list[str]:
    lines: list[str] = []
    for i, hit in enumerate(hits, start=1):
        tag = f"  {hit.method}" if hit.method else ""
        if hit.ok and hit.latency_ms is not None:
            lines.append(f"{indent}  {i:>3}  {hit.latency_ms:.1f}ms{tag}")
        else:
            cls = hit.error_class or "error"
            msg = hit.error or ""
            lat = f"{hit.latency_ms:.1f}ms  " if hit.latency_ms is not None else ""
            lines.append(f"{indent}  {i:>3}  {lat}{cls}  {msg}{tag}".rstrip())
    return lines


def _capability_lines(result: RunResult, ranked: tuple[EndpointOutcome, ...]) -> list[str]:
    ok_names = [o.endpoint.name for o in ranked if o.stats.n_ok]
    miss = [o for o in ranked if not o.stats.n_ok]
    total = len(ranked)
    lines = [f"  {result.method}  {len(ok_names)}/{total} responded"]
    if miss:
        bits = []
        for outcome in miss:
            cls = "error"
            if outcome.stats.by_class:
                cls = outcome.stats.by_class[0][0]
            elif _last_error(outcome):
                cls = _last_error(outcome).split(":", 1)[0]
            bits.append(f"{outcome.endpoint.name} ({cls})")
        lines.append(f"  missed     {', '.join(bits)}")
    return lines


def _ranking_entry(row: RankedPlace, rank_by: str) -> dict[str, Any]:
    outcome = row.outcome
    stats = outcome.stats
    return {
        "rank": row.rank,
        "similar": row.similar,
        "reliable": row.reliable,
        "name": outcome.endpoint.name,
        "ok": stats.n_ok > 0,
        "n_ok": stats.n_ok,
        "n_fail": stats.n_fail,
        "error_rate": stats.error_rate,
        "errors": dict(stats.by_class),
        "mean_ms": stats.mean_ms,
        "p50_ms": stats.p50_ms,
        "p95_ms": stats.p95_ms,
        "p99_ms": stats.p99_ms,
        "p99_reliable": row.p99_reliable,
        "jitter_ms": stats.jitter_ms,
        "rps": (1000.0 / stats.mean_ms) if stats.mean_ms else None,
        "rank_by": rank_by,
        "rank_value": _rank_value(stats, rank_by),
        "score": _success_rate(stats.error_rate),
    }


def _provider_entry(row: RankedPlace, method: str) -> dict[str, Any]:
    outcome = row.outcome
    stats = outcome.stats
    success = _success_rate(stats.error_rate)
    rps = (1000.0 / stats.mean_ms) if stats.mean_ms else None
    return {
        "name": outcome.endpoint.name,
        "url": outcome.endpoint.display_url,
        "id": outcome.endpoint.url_id,
        "rank": row.rank,
        "similar": row.similar,
        "reliable": row.reliable,
        "ok": stats.n_ok > 0,
        "performance": {
            "n_ok": stats.n_ok,
            "n_fail": stats.n_fail,
            "min_ms": stats.min_ms,
            "mean_ms": stats.mean_ms,
            "max_ms": stats.max_ms,
            "p50_ms": stats.p50_ms,
            "p95_ms": stats.p95_ms,
            "p99_ms": stats.p99_ms,
            "p99_reliable": row.p99_reliable,
            "jitter_ms": stats.jitter_ms,
            "rps": rps,
            "histogram": _histogram_json(stats.histogram),
        },
        "errors": {
            "error_rate": stats.error_rate,
            "by_class": dict(stats.by_class),
        },
        "reliability": {
            "success_rate": success,
            "score": success,
        },
        "capability": {
            "method": method,
            "responded": stats.n_ok > 0,
        },
        "last_error": _last_error(outcome) or None,
        "warmup": [_hit_entry(hit) for hit in outcome.warmup],
        "samples": [_hit_entry(hit) for hit in outcome.samples],
    }


def _hit_entry(hit: Any) -> dict[str, Any]:
    return {
        "ok": hit.ok,
        "reachable": hit.reachable,
        "latency_ms": hit.latency_ms,
        "error": hit.error,
        "error_class": hit.error_class,
        "attempts": hit.attempts,
        "method": hit.method,
    }


def _success_rate(error_rate: float | None) -> float | None:
    if error_rate is None:
        return None
    return 1.0 - error_rate


def _miss_class(outcome: EndpointOutcome) -> str:
    if outcome.stats.by_class:
        return outcome.stats.by_class[0][0]
    err = _last_error(outcome)
    if err:
        return err.split(":", 1)[0]
    return "error"


def _last_error(outcome: EndpointOutcome) -> str:
    hit = outcome.samples[-1] if outcome.samples else (
        outcome.warmup[-1] if outcome.warmup else None
    )
    if hit is None or not hit.error:
        return ""
    return f"{hit.error_class}: {hit.error}"


def _pct(rate: float | None) -> str:
    if rate is None:
        return "n/a"
    return f"{100 * rate:.0f}%"


def _concurrency_label(concurrency: int) -> str:
    if concurrency <= 0:
        return "all"
    return str(concurrency)


def _jitter_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}ms"


def _histogram_text(histogram: tuple[tuple[str, int], ...]) -> str:
    return "  ".join(f"{label}={count}" for label, count in histogram)


def _histogram_bucket_defs() -> list[dict[str, Any]]:
    edges: tuple[float | None, ...] = (*HISTOGRAM_EDGES_MS, None)
    return [
        {"label": label, "lt_ms": edge}
        for label, edge in zip(HISTOGRAM_LABELS, edges, strict=True)
    ]


def _histogram_json(histogram: tuple[tuple[str, int], ...]) -> list[dict[str, Any]]:
    edges: tuple[float | None, ...] = (*HISTOGRAM_EDGES_MS, None)
    return [
        {"label": label, "lt_ms": edge, "n": count}
        for (label, count), edge in zip(histogram, edges, strict=True)
    ]
