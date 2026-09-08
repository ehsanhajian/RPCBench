"""Human-readable CLI report and machine-readable JSON. No security findings."""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any

from rpcbench import __version__
from rpcbench.coverage import (
    as_dict as coverage_dict,
    cell_for,
    coverage_steps,
    is_coverage_miss,
    missed_steps,
)
from rpcbench.recommend import Route, recommend as recommend_route
from rpcbench.reliability import assess as assess_reliability
from rpcbench.run import (
    EndpointOutcome,
    HISTOGRAM_EDGES_MS,
    HISTOGRAM_LABELS,
    RunResult,
    percentile,
)
from rpcbench.verdict import (
    NOT_READY,
    READY,
    RISKY,
    Verdict,
    assess as assess_verdict,
)
from rpcbench.watermark import (
    DOCS_BOUNDARY,
    DOCS_METHODOLOGY,
    as_dict as watermark_dict,
    cite_line,
)

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


def is_stale(outcome: EndpointOutcome) -> bool:
    fresh = outcome.freshness
    return fresh is not None and fresh.verdict == "stale"


def is_disagree(outcome: EndpointOutcome) -> bool:
    cons = outcome.consistency
    return cons is not None and cons.verdict == "disagree"


def reliable_for_place(
    stats: Any, similar_band: float, outcome: EndpointOutcome | None = None
) -> bool:
    if stats.n_ok == 0:
        return False
    if stats.error_rate is not None and stats.error_rate > similar_band:
        return False
    if outcome is not None and (
        is_stale(outcome) or is_disagree(outcome) or is_coverage_miss(outcome)
    ):
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
        tier = 0 if reliable_for_place(stats, band, outcome) else 1
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
        if not reliable_for_place(stats, band, outcome):
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


_ANSI = re.compile(r"\033\[[0-9;]*m")


def _visible_len(text: str) -> int:
    return len(_ANSI.sub("", text))


def _pad_visible(text: str, width: int, *, right: bool) -> str:
    fill = width - _visible_len(text)
    if fill <= 0:
        return text
    return (" " * fill + text) if right else (text + " " * fill)


def _grid(
    headers: list[str],
    rows: list[list[str]],
    *,
    right: tuple[bool, ...] | None = None,
) -> list[str]:
    """Box-draw a table. ``right[i]`` right-aligns that column (numbers)."""
    cols = len(headers)
    align = right or tuple(False for _ in headers)
    widths = [_visible_len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _visible_len(cell))

    def rule(left: str, mid: str, right_ch: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right_ch

    def line(cells: list[str]) -> str:
        parts = [
            " " + _pad_visible(cells[i], widths[i], right=align[i]) + " "
            for i in range(cols)
        ]
        return "│" + "│".join(parts) + "│"

    out = [
        "  " + rule("┌", "┬", "┐"),
        "  " + line(headers),
        "  " + rule("├", "┼", "┤"),
    ]
    for row in rows:
        padded = list(row) + [""] * (cols - len(row))
        out.append("  " + line(padded[:cols]))
    out.append("  " + rule("└", "┴", "┘"))
    return out


def _name_cell(outcome: EndpointOutcome, use_color: bool) -> str:
    ok = outcome.stats.n_ok > 0
    return _paint(
        outcome.endpoint.name, _GREEN if ok else _RED, enabled=use_color
    )


def _status_cell(outcome: EndpointOutcome, use_color: bool) -> str:
    ok = outcome.stats.n_ok > 0
    return _paint("ok" if ok else "fail", _GREEN if ok else _RED, enabled=use_color)


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
    marks = _verdict_map(placed, result, band)
    route = _route_for(placed, marks)
    ok_rows = [o for o in ranked if o.stats.n_ok]
    fail_rows = [o for o in ranked if not o.stats.n_ok]
    winners = [row for row in placed if row.rank == 1]
    ranking: list[dict[str, Any]] = []
    providers: list[dict[str, Any]] = []
    for row in placed:
        name = row.outcome.endpoint.name
        ranking.append(_ranking_entry(row, rank_by, marks[name]))
        providers.append(_provider_entry(row, result.method, marks[name]))
    return {
        "tool": "rpcbench",
        "version": __version__,
        "schema": SCHEMA_VERSION,
        "watermark": watermark_dict(result),
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
        "burst": result.burst,
        "rps": result.rps,
        "batch": result.batch,
        "connection": result.connection,
        "http": result.http,
        "mode": result.mode,
        "seed": result.seed,
        "sequence_id": result.sequence_id,
        "rank_by": rank_by,
        "similar_band": band,
        "stale_blocks": result.stale_blocks,
        "block_time_s": result.block_time_s,
        "cohort_height": result.cohort_height,
        "pin_height": result.pin_height,
        "canonical_hash": result.canonical_hash,
        "histogram_buckets": _histogram_bucket_defs(),
        "summary": {
            "fastest": winners[0].outcome.endpoint.name if len(winners) == 1 else None,
            "fastest_names": [row.outcome.endpoint.name for row in winners],
            "fastest_similar": len(winners) > 1,
            "ok": len(ok_rows),
            "failed": len(fail_rows),
            "failed_names": [o.endpoint.name for o in fail_rows],
            "stale_names": [o.endpoint.name for o in ranked if is_stale(o)],
            "disagree_names": [o.endpoint.name for o in ranked if is_disagree(o)],
            "coverage_miss_names": [
                o.endpoint.name for o in ranked if is_coverage_miss(o)
            ],
            "ready_names": [
                row.outcome.endpoint.name
                for row in placed
                if marks[row.outcome.endpoint.name].decision == READY
            ],
            "risky_names": [
                row.outcome.endpoint.name
                for row in placed
                if marks[row.outcome.endpoint.name].decision == RISKY
            ],
            "not_ready_names": [
                row.outcome.endpoint.name
                for row in placed
                if marks[row.outcome.endpoint.name].decision == NOT_READY
            ],
            "primary": route.primary,
            "fallback": route.fallback,
        },
        "route": route.as_dict(),
        "comparison": [
            _comparison_entry(
                outcome,
                result.method,
                marks[outcome.endpoint.name],
            )
            for outcome in result.outcomes
        ],
        "ranking": ranking,
        "methods": _methods_json(result),
        "coverage": coverage_dict(result),
        "tags": _tags_json(result),
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
    marks = _verdict_map(placed, result, band)
    route = _route_for(placed, marks)
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
        f"concurrency={_concurrency_label(result.concurrency)}"
        f"{_burst_mode_suffix(result)}"
        f"{_batch_mode_suffix(result)}"
        f"  ·  conn={result.connection}",
        cite_line(result),
        "",
        "Summary",
    ]
    lines.extend(
        _summary_lines(
            placed,
            fail_rows,
            len(result.outcomes),
            use_color,
            rank_by,
            band,
            result,
        )
    )
    lines.extend(_verdict_summary_lines(placed, marks, len(result.outcomes), use_color))
    lines.extend(_route_lines(route, use_color))
    name_w = max((len(o.endpoint.name) for o in result.outcomes), default=4)
    lines.extend(
        [
            "",
            f"Ranking  (by {label}; similar within {band_pct}; "
            "~ high err, stale, disagree, or miss; failed last)",
        ]
    )
    lines.extend(_ranking_lines(placed, name_w, use_color, rank_by))
    lines.extend(_exception_lines(result, ranked))
    mix = result.profile == "mix" or len(result.workload) > 1
    if verbose:
        lines.extend(
            _verbose_sections(
                result,
                ranked,
                name_w,
                use_color,
                compare_what,
                placed,
                marks,
            )
        )
    else:
        if mix:
            lines.extend(_coverage_section(result, use_color))
        if result.batch > 0:
            lines.extend(_batch_section(result, use_color))
    extra_p99 = ""
    if any(not row.p99_reliable and row.outcome.stats.n_ok for row in placed):
        extra_p99 = f"  ·  P99 is the slowest sample until n≥{P99_MIN_N}; need ≥{P99_MIN_N}"
    lines.append("")
    lines.append(
        _footer_line(
            result,
            ok_rows,
            fail_rows,
            band_pct,
            extra_p99,
            verbose=verbose,
        )
    )
    if verbose:
        lines.append(
            "Not an SLA or a security audit  ·  "
            f"{DOCS_METHODOLOGY}  ·  {DOCS_BOUNDARY}"
        )
    return "\n".join(lines) + "\n"


def _exception_lines(
    result: RunResult, ranked: tuple[EndpointOutcome, ...]
) -> list[str]:
    """Throttle and coverage misses that compact mode would otherwise hide."""
    rows: list[str] = []
    for outcome in ranked:
        timed = _rate_limit_n(outcome.stats)
        tags = _tag_rate_limit_n(outcome)
        missed = ()
        if result.profile == "mix" or len(result.workload) > 1:
            missed = missed_steps(outcome, result)
        if not timed and not tags and not missed:
            continue
        bits = []
        if timed:
            bits.append(f"rate_limit={timed}")
        if tags:
            bits.append(f"tags={tags}")
        if missed:
            bits.append("miss=" + ",".join(missed))
        rows.append(f"  {outcome.endpoint.name}  " + "  ".join(bits))
    if not rows:
        return []
    return ["", "Notes"] + rows


def _verbose_sections(
    result: RunResult,
    ranked: tuple[EndpointOutcome, ...],
    name_w: int,
    use_color: bool,
    compare_what: str,
    placed: tuple[RankedPlace, ...],
    marks: dict[str, Verdict],
) -> list[str]:
    lines: list[str] = [
        "",
        f"Comparison  (config order · {result.mode} · same {compare_what}, samples, and budget)",
    ]
    lines.extend(_comparison_lines(result, name_w, use_color))
    lines.extend(
        [
            "",
            "Reliability  (0–100 this run; errors, timeouts, p99/p50, mix coverage; "
            "not an SLA or a security score)",
        ]
    )
    lines.extend(_reliability_lines(result, use_color))
    lines.extend(_signal_lines(placed, marks, use_color))
    lines.extend(_coverage_section(result, use_color))
    if result.profile == "mix" or len(result.workload) > 1:
        lines.extend(["", "Methods  (per-method; ranking uses the whole mix)"])
        lines.extend(_methods_lines(result, name_w, use_color))
    if any(outcome.timing for outcome in result.outcomes):
        lines.extend(
            [
                "",
                "Timing  (handshake = DNS+TCP+TLS; server = wait after connect; "
                "payload = body+parse; p95 of successes; not mixed into ranking; "
                f"conn={result.connection})",
            ]
        )
        lines.extend(_timing_lines(result, name_w, use_color))
    if any(outcome.transport for outcome in result.outcomes):
        lines.extend(
            [
                "",
                "Transport  (negotiated proto, content-encoding, bytes in/out; "
                f"not mixed into ranking; http={result.http})",
            ]
        )
        lines.extend(_transport_lines(result, name_w, use_color))
    if result.batch > 0:
        lines.extend(_batch_section(result, use_color))
    if any(outcome.tags for outcome in result.outcomes):
        lines.extend(
            ["", "Tags  (latest / safe / finalized snapshot; not mixed into ranking)"]
        )
        lines.extend(_tags_lines(result, name_w, use_color))
    if result.burst > 0:
        lines.extend(
            [
                "",
                "Burst  (first "
                f"{result.burst} timed samples overlap; then "
                f"{_rps_label(result.rps)}; same request budget; "
                "tag throttles as tags=N)",
            ]
        )
        lines.extend(_burst_lines(result, name_w, use_color))
    lines.extend(["", "Providers  (url redacted; hist = successful samples in each bucket)"])
    lines.extend(_provider_table(ranked, name_w, use_color))
    for outcome in ranked:
        lines.extend(_provider_verbose_lines(outcome, name_w))
    lines.extend(["", "Capabilities"])
    lines.extend(_capability_lines(result, ranked))
    return lines


def _footer_line(
    result: RunResult,
    ok_rows: list[EndpointOutcome],
    fail_rows: list[EndpointOutcome],
    band_pct: str,
    extra_p99: str,
    *,
    verbose: bool,
) -> str:
    counts = f"{len(ok_rows)} ok  {len(fail_rows)} failed  ·  warmup excluded"
    if not verbose:
        return f"{counts}  ·  --verbose for full report{extra_p99}"
    return (
        f"{counts}  ·  "
        "err=failed/attempted  ·  min/mean/max, jitter (stddev), p50/p95/p99, "
        f"and histogram of successful samples  ·  similar-band {band_pct}  ·  "
        f"stale >{result.stale_blocks} blocks vs cohort median "
        f"({result.block_time_s:g}s/block)  ·  "
        f"hash at block {result.pin_height if result.pin_height is not None else '—'}  ·  "
        "client is a label only  ·  "
        "rate_limit is 429 / CU throttle  ·  "
        "handshake is DNS+TCP+TLS (0 on keep-alive reuse)"
        f"{extra_p99}"
    )


def _summary_lines(
    placed: tuple[RankedPlace, ...] | list[RankedPlace],
    fail_rows: list[EndpointOutcome],
    total: int,
    use_color: bool,
    rank_by: str,
    similar_band: float,
    result: RunResult,
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
    stale_rows = [row.outcome for row in placed if is_stale(row.outcome)]
    if stale_rows:
        names = ", ".join(o.endpoint.name for o in stale_rows)
        lines.append(f"  Stale    {len(stale_rows)}/{total}    {names}")
    disagree_rows = [row.outcome for row in placed if is_disagree(row.outcome)]
    if disagree_rows:
        names = ", ".join(o.endpoint.name for o in disagree_rows)
        lines.append(f"  Disagree {len(disagree_rows)}/{total}    {names}")
    miss_rows = [row.outcome for row in placed if is_coverage_miss(row.outcome)]
    if miss_rows:
        bits = []
        for outcome in miss_rows:
            steps = ",".join(missed_steps(outcome, result))
            if steps:
                bits.append(f"{outcome.endpoint.name} ({steps})")
            else:
                bits.append(outcome.endpoint.name)
        lines.append(f"  Miss     {len(miss_rows)}/{total}    {', '.join(bits)}")
    return lines


def _verdict_map(
    placed: tuple[RankedPlace, ...] | list[RankedPlace],
    result: RunResult,
    similar_band: float,
) -> dict[str, Verdict]:
    return {
        row.outcome.endpoint.name: assess_verdict(
            row.outcome,
            rank=row.rank,
            similar=row.similar,
            similar_band=similar_band,
            result=result,
        )
        for row in placed
    }


def _route_for(
    placed: tuple[RankedPlace, ...] | list[RankedPlace],
    marks: dict[str, Verdict],
) -> Route:
    return recommend_route(
        [
            (row.outcome, row.rank, marks[row.outcome.endpoint.name])
            for row in placed
        ]
    )


def _route_lines(route: Route, use_color: bool) -> list[str]:
    lines = ["", "Route  (this workload, this run; not an SLA)"]
    primary = (
        _paint(route.primary, _BOLD, _GREEN, enabled=use_color)
        if route.primary
        else "none"
    )
    fallback = (
        _paint(route.fallback, _GREEN, enabled=use_color)
        if route.fallback
        else "none"
    )
    lines.append(f"  Primary   {primary}")
    lines.append(f"  Fallback  {fallback}")
    lines.append(f"  {route.why}")
    return lines


def _verdict_summary_lines(
    placed: tuple[RankedPlace, ...] | list[RankedPlace],
    marks: dict[str, Verdict],
    total: int,
    use_color: bool,
) -> list[str]:
    lines = ["", "Verdict  (this workload, this run; not an SLA)"]
    groups = (
        (READY, "ready", _GREEN),
        (RISKY, "risky", _RED),
        (NOT_READY, "not ready", _RED),
    )
    for key, label, color in groups:
        rows = [
            row
            for row in placed
            if marks[row.outcome.endpoint.name].decision == key
        ]
        if not rows:
            continue
        if key == READY:
            names = ", ".join(row.outcome.endpoint.name for row in rows)
        else:
            names = ", ".join(
                f"{row.outcome.endpoint.name} ({marks[row.outcome.endpoint.name].kind})"
                for row in rows
            )
        painted = _paint(label, color, enabled=use_color)
        lines.append(
            f"  {_pad_visible(painted, 9, right=False)} {len(rows)}/{total}    {names}"
        )
    return lines


def _signal_lines(
    placed: tuple[RankedPlace, ...] | list[RankedPlace],
    marks: dict[str, Verdict],
    use_color: bool,
) -> list[str]:
    lines = [
        "",
        "Signals  (problem / why / next; routing and config, not hardening)",
    ]
    found = False
    for row in placed:
        verdict = marks[row.outcome.endpoint.name]
        if not verdict.signals:
            continue
        found = True
        name = _paint(
            row.outcome.endpoint.name,
            _GREEN if verdict.decision == READY else _RED,
            enabled=use_color,
        )
        lines.append(f"  {name}  {verdict.cli_decision()}  {verdict.kind}")
        for sig in verdict.signals:
            lines.append(f"    problem  {sig.problem}")
            lines.append(f"    why      {sig.why}")
            lines.append(f"    next     {sig.next}")
    if not found:
        lines.append("  none")
    return lines


def _method_header(result: RunResult, params: str) -> str:
    if result.profile == "mix" and result.workload:
        steps = ", ".join(spec.name for spec in result.workload)
        return f"Method    mix  ·  {steps}"
    return f"Method    {result.method}{params}"


def _coverage_section(result: RunResult, use_color: bool) -> list[str]:
    return [
        "",
        "Coverage  (active workload only; ok / error class / skip if not offered; not a scan)",
        *_coverage_lines(result, use_color),
    ]


def _coverage_lines(result: RunResult, use_color: bool) -> list[str]:
    steps = coverage_steps(result)
    headers = ["name", *[spec.name for spec in steps]]
    rows: list[list[str]] = []
    for outcome in result.outcomes:
        cells = [_name_cell(outcome, use_color)]
        for spec in steps:
            cell = cell_for(outcome, spec, result)
            label = cell.label()
            ok = cell.status == "ok"
            cells.append(_paint(label, _GREEN if ok else _RED, enabled=use_color))
        rows.append(cells)
    right = (False,) + tuple(True for _ in steps)
    return _grid(headers, rows, right=right)


def _reliability_lines(result: RunResult, use_color: bool) -> list[str]:
    rows: list[list[str]] = []
    for outcome in result.outcomes:
        rel = assess_reliability(outcome)
        ratio = "—" if rel.p99_p50 is None else f"{rel.p99_p50:.2f}"
        rows.append(
            [
                _name_cell(outcome, use_color),
                str(rel.score),
                f"{rel.errors:.0f}",
                f"{rel.timeouts:.0f}",
                f"{rel.tail:.0f}",
                f"{rel.coverage_points:.0f}",
                ratio,
            ]
        )
    return _grid(
        ["name", "rel", "errors", "timeouts", "tail", "cov", "p99/p50"],
        rows,
        right=(False, True, True, True, True, True, True),
    )


def _methods_lines(
    result: RunResult, name_w: int, use_color: bool
) -> list[str]:
    lookup = {spec.name: spec.method for spec in result.workload}
    rows: list[list[str]] = []
    for outcome in result.outcomes:
        name = _name_cell(outcome, use_color)
        for step, stats in outcome.by_method:
            attempted = stats.n_ok + stats.n_fail
            n = f"{stats.n_ok}/{attempted}" if attempted else "—"
            rows.append(
                [
                    name,
                    step,
                    lookup.get(step, step),
                    n,
                    _pct(stats.error_rate),
                    _cell_ms(stats.p50_ms),
                    _cell_ms(stats.p95_ms),
                    _cell_ms(stats.p99_ms),
                ]
            )
    return _grid(
        ["endpoint", "step", "method", "n", "err", "p50", "p95", "p99"],
        rows,
        right=(False, False, False, True, True, True, True, True),
    )


def _timing_lines(
    result: RunResult, name_w: int, use_color: bool
) -> list[str]:
    rows: list[list[str]] = []
    dash = _cell_ms(None)
    for outcome in result.outcomes:
        name = _name_cell(outcome, use_color)
        summary = outcome.timing
        if summary is None:
            rows.append(
                [name, "—", dash, dash, dash, dash, dash, dash, dash, dash]
            )
            continue
        rows.append(
            [
                name,
                str(summary.handshake.n),
                _cell_ms(summary.handshake.p95_ms),
                _cell_ms(summary.server.p95_ms),
                _cell_ms(summary.payload.p95_ms),
                _cell_ms(summary.dns.p95_ms),
                _cell_ms(summary.tcp.p95_ms),
                _cell_ms(summary.tls.p95_ms),
                _cell_ms(summary.body.p95_ms),
                _cell_ms(summary.parse.p95_ms),
            ]
        )
    return _grid(
        [
            "name",
            "n",
            "handshake",
            "server",
            "payload",
            "dns",
            "tcp",
            "tls",
            "body",
            "parse",
        ],
        rows,
        right=(False, True, True, True, True, True, True, True, True, True),
    )


def _fmt_bytes(value: float | None) -> str:
    if value is None:
        return "—"
    n = int(round(value))
    if n < 1000:
        return f"{n}B"
    if n < 1_000_000:
        return f"{n / 1000:.1f}kB"
    return f"{n / 1_000_000:.1f}MB"


def _transport_lines(
    result: RunResult, name_w: int, use_color: bool
) -> list[str]:
    rows: list[list[str]] = []
    for outcome in result.outcomes:
        name = _name_cell(outcome, use_color)
        summary = outcome.transport
        if summary is None:
            rows.append([name, "—", "—", "—", "—", "—"])
            continue
        rows.append(
            [
                name,
                summary.http_version or "—",
                summary.encoding or "—",
                _fmt_bytes(summary.bytes_out_mean),
                _fmt_bytes(summary.bytes_in_mean),
                _fmt_bytes(summary.bytes_in_p95),
            ]
        )
    return _grid(
        ["name", "proto", "enc", "out", "in", "in_p95"],
        rows,
        right=(False, False, False, True, True, True),
    )


def _batch_section(result: RunResult, use_color: bool) -> list[str]:
    return [
        "",
        "Batch  ("
        f"{result.batch} calls in one POST vs the same {result.batch} "
        "sent one-by-one; extra read; not mixed into ranking)",
        *_batch_lines(result, use_color),
    ]


def _batch_lines(result: RunResult, use_color: bool) -> list[str]:
    rows: list[list[str]] = []
    for outcome in result.outcomes:
        name = _name_cell(outcome, use_color)
        summary = outcome.batch
        if summary is None:
            rows.append([name, "—", "—", "—", "—", "—"])
            continue
        rows.append(
            [
                name,
                _batch_support_cell(summary, use_color),
                _cell_ms(summary.batch_ms),
                _cell_ms(summary.serial_ms),
                _fmt_ratio(summary.ratio),
                _batch_items_cell(summary),
            ]
        )
    return _grid(
        ["name", "support", "batch", "serial", "ratio", "items"],
        rows,
        right=(False, False, True, True, True, True),
    )


_BATCH_SKIP = frozenset({"budget", "duration"})


def batch_support_label(summary: Any) -> str:
    """yes / partial / no / skip. skip = extra read did not run."""
    if summary is None:
        return "—"
    if isinstance(summary, dict):
        error_class = summary.get("error_class")
        supported = bool(summary.get("supported"))
        partial = bool(summary.get("partial"))
    else:
        error_class = summary.error_class
        supported = bool(summary.supported)
        partial = bool(summary.partial)
    if error_class in _BATCH_SKIP:
        return "skip"
    if not supported:
        return "no"
    if partial:
        return "partial"
    return "yes"


def _batch_support_cell(summary: Any, use_color: bool) -> str:
    label = batch_support_label(summary)
    if label == "yes":
        return _paint(label, _GREEN, enabled=use_color)
    if label == "no":
        return _paint(label, _RED, enabled=use_color)
    return label


def _batch_items_cell(summary: Any) -> str:
    if batch_support_label(summary) in {"no", "skip"}:
        return summary.error_class or "—"
    return f"{summary.n_ok}/{summary.size}"


def _fmt_ratio(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}×"


def _tags_lines(
    result: RunResult, name_w: int, use_color: bool
) -> list[str]:
    rows: list[list[str]] = []
    for outcome in result.outcomes:
        name = _name_cell(outcome, use_color)
        for snap in outcome.tags:
            if snap.skipped:
                rows.append(
                    [
                        name,
                        snap.tag,
                        _cell_ms(snap.latency_ms),
                        "—",
                        "—",
                        "—",
                        snap.skip_reason or "skip",
                    ]
                )
                continue
            fresh = snap.freshness
            lag = (
                "—"
                if fresh is None or fresh.lag_blocks is None
                else str(fresh.lag_blocks)
            )
            verdict = "—"
            if fresh is not None:
                if fresh.verdict == "stale":
                    verdict = "stale"
                elif fresh.verdict == "fresh":
                    verdict = "yes"
            height = "—" if snap.height is None else str(snap.height)
            rows.append(
                [
                    name,
                    snap.tag,
                    _cell_ms(snap.latency_ms),
                    height,
                    lag,
                    verdict,
                    "",
                ]
            )
    return _grid(
        ["endpoint", "tag", "lat", "head", "lag", "fresh", "note"],
        rows,
        right=(False, False, True, True, True, False, False),
    )


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
                    **_method_transport(outcome, lookup.get(step, step)),
                }
            )
    return rows


def _method_transport(outcome: EndpointOutcome, method: str) -> dict[str, Any]:
    hits = [
        hit
        for hit in outcome.samples
        if (hit.method or "") == method and hit.ok
    ]
    incoming = [float(hit.bytes_in) for hit in hits if hit.bytes_in is not None]
    outgoing = [float(hit.bytes_out) for hit in hits if hit.bytes_out is not None]
    encodings = [hit.encoding for hit in hits if hit.encoding]
    counts: Counter[str] = Counter(encodings)
    return {
        "bytes_in_p95": percentile(incoming, 0.95) if incoming else None,
        "bytes_out_mean": (sum(outgoing) / len(outgoing)) if outgoing else None,
        "encoding": counts.most_common(1)[0][0] if counts else None,
    }


def _tags_json(result: RunResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for outcome in result.outcomes:
        for snap in outcome.tags:
            rows.append(_tag_entry(outcome.endpoint.name, snap))
    return rows


def _tag_entry(name: str, snap: Any) -> dict[str, Any]:
    fresh = snap.freshness
    return {
        "name": name,
        "tag": snap.tag,
        "ok": not snap.skipped,
        "latency_ms": snap.latency_ms,
        "height": snap.height,
        "hash": snap.hash,
        "skipped": snap.skipped,
        "skip_reason": snap.skip_reason,
        "freshness": None
        if fresh is None
        else {
            "height": fresh.height,
            "height_hex": fresh.height_hex,
            "lag_blocks": fresh.lag_blocks,
            "lag_s": fresh.lag_s,
            "verdict": fresh.verdict,
            "cohort_height": fresh.cohort_height,
        },
    }


def _comparison_lines(
    result: RunResult, name_w: int, use_color: bool
) -> list[str]:
    rows = [
        _comparison_cells(outcome, use_color) for outcome in result.outcomes
    ]
    return _grid(
        [
            "name",
            "status",
            "n",
            "err",
            "rel",
            "p50",
            "p95",
            "p99",
            "jit",
            "rps",
            "head",
            "lag",
            "fresh",
            "hash",
            "match",
            "cap",
        ],
        rows,
        right=(
            False,
            False,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            False,
            False,
            False,
            False,
        ),
    )


def _comparison_cells(outcome: EndpointOutcome, use_color: bool) -> list[str]:
    stats = outcome.stats
    ok = stats.n_ok > 0
    attempted = stats.n_ok + stats.n_fail
    cap = "yes" if ok else _miss_class(outcome)
    return [
        _name_cell(outcome, use_color),
        _status_cell(outcome, use_color),
        f"{stats.n_ok}/{attempted}",
        _pct(stats.error_rate),
        str(assess_reliability(outcome).score),
        _cell_ms(stats.p50_ms),
        _cell_ms(stats.p95_ms),
        _cell_ms(stats.p99_ms),
        _cell_ms(stats.jitter_ms),
        _cell_rps(stats.mean_ms),
        _cell_head(outcome),
        _cell_lag(outcome),
        _cell_fresh(outcome).strip(),
        _cell_hash(outcome).strip(),
        _cell_agree(outcome).strip(),
        cap,
    ]


def _cell_ms(value: float | None) -> str:
    if value is None:
        return f"{'—':>8}"
    return f"{value:6.1f}ms"


def _cell_head(outcome: EndpointOutcome) -> str:
    fresh = outcome.freshness
    if fresh is None or fresh.height is None:
        return f"{'—':>8}"
    return f"{fresh.height:>8}"


def _cell_lag(outcome: EndpointOutcome) -> str:
    fresh = outcome.freshness
    if fresh is None or fresh.lag_blocks is None:
        return f"{'—':>4}"
    return f"{fresh.lag_blocks:>4}"


def _cell_fresh(outcome: EndpointOutcome) -> str:
    fresh = outcome.freshness
    if fresh is None:
        return f"{'—':<5}"
    if fresh.verdict == "stale":
        return f"{'stale':<5}"
    if fresh.verdict == "fresh":
        return f"{'yes':<5}"
    return f"{'—':<5}"


def _cell_hash(outcome: EndpointOutcome) -> str:
    cons = outcome.consistency
    digest = None if cons is None else cons.hash
    if digest is None:
        return f"{'—':<10}"
    return f"{digest[:10]:<10}"


def _cell_agree(outcome: EndpointOutcome) -> str:
    cons = outcome.consistency
    if cons is None:
        return f"{'—':<5}"
    if cons.verdict == "disagree":
        return f"{'no':<5}"
    if cons.verdict == "agree":
        return f"{'yes':<5}"
    return f"{'—':<5}"


def _cell_rps(mean_ms: float | None) -> str:
    if not mean_ms:
        return f"{'—':>6}"
    return f"{1000.0 / mean_ms:>6.1f}"


def _comparison_entry(
    outcome: EndpointOutcome, method: str, verdict: Verdict
) -> dict[str, Any]:
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
        "reliability": assess_reliability(outcome).as_dict(),
        "verdict": verdict.as_dict(),
        "freshness": _freshness_json(outcome),
        "consistency": _consistency_json(outcome),
        "timing": _timing_summary_json(outcome.timing),
        "transport": _transport_summary_json(outcome.transport),
        "batch": _batch_summary_json(outcome.batch),
    }


def _rank_metric_text(stats: Any, rank_by: str) -> str:
    value = _rank_value(stats, rank_by)
    if value is None:
        return f"{_RANK_LABELS[rank_by]}=n/a"
    if rank_by == "rps":
        return f"rps={value:.1f}"
    return f"{_RANK_LABELS[rank_by]}={value:.1f}ms"


def _ranking_lines(
    placed: tuple[RankedPlace, ...] | list[RankedPlace],
    name_w: int,
    use_color: bool,
    rank_by: str,
) -> list[str]:
    rows: list[list[str]] = []
    for row in placed:
        if row.rank is not None:
            mark = str(row.rank)
        elif row.outcome.stats.n_ok:
            mark = "~"
        else:
            mark = "—"
        rows.append(_ranking_cells(row.outcome, mark, use_color, rank_by))
    return _grid(
        ["#", "name", "status", "n", "err", "rel", "p95", "mean", "jit", "note"],
        rows,
        right=(True, False, False, True, True, True, True, True, True, False),
    )


def _ranking_cells(
    outcome: EndpointOutcome,
    mark: str,
    use_color: bool,
    rank_by: str,
) -> list[str]:
    stats = outcome.stats
    ok = stats.n_ok > 0
    attempted = stats.n_ok + stats.n_fail
    note = _row_note(outcome)
    if rank_by not in {"p95", "mean"} and ok:
        metric = _rank_metric_text(stats, rank_by)
        note = metric if note == "—" else f"{note}  {metric}"
    return [
        mark,
        _name_cell(outcome, use_color),
        _status_cell(outcome, use_color),
        f"{stats.n_ok}/{attempted}",
        _pct(stats.error_rate),
        str(assess_reliability(outcome).score),
        _cell_ms(stats.p95_ms),
        _cell_ms(stats.mean_ms),
        _cell_ms(stats.jitter_ms),
        note,
    ]


def _row_note(outcome: EndpointOutcome) -> str:
    stats = outcome.stats
    bits = [f"{name}={count}" for name, count in stats.by_class]
    if stats.n_ok == 0:
        err = _last_error(outcome)
        if err:
            bits.append(err)
        return "  ".join(bits) if bits else (_miss_class(outcome) or "—")
    if is_stale(outcome):
        bits.append("stale")
        fresh = outcome.freshness
        if fresh is not None and fresh.lag_s is not None:
            bits.append(f"~{fresh.lag_s:g}s")
    if is_disagree(outcome):
        bits.append("disagree")
    missed = [name for name, stats in outcome.by_method if stats.n_ok == 0]
    if missed:
        bits.append("miss=" + ",".join(missed))
    tags = _tag_rate_limit_n(outcome)
    if tags:
        bits.append(f"tags={tags}")
    return "  ".join(bits) if bits else "—"


def _clip(text: str, width: int) -> str:
    if len(text) <= width:
        return f"{text:<{width}}"
    if width <= 3:
        return text[:width]
    return text[: width - 1] + "…"


def _hist_counts(histogram: tuple[tuple[str, int], ...]) -> str:
    bits = [f"{label}={count}" for label, count in histogram if count]
    return "  ".join(bits) if bits else "—"


def _provider_table(
    ranked: tuple[EndpointOutcome, ...], name_w: int, use_color: bool
) -> list[str]:
    urls = [outcome.endpoint.display_url for outcome in ranked] or [""]
    url_w = min(max(len(url) for url in urls), 42)
    clients = [(outcome.client or "—") for outcome in ranked] or ["—"]
    client_w = min(max(len(text) for text in clients), 22)
    rows = [
        _provider_cells(outcome, url_w, client_w, use_color) for outcome in ranked
    ]
    return _grid(
        [
            "name",
            "status",
            "url",
            "client",
            "n",
            "err",
            "rel",
            "p95",
            "head",
            "lag",
            "fresh",
            "match",
            "hist",
            "note",
        ],
        rows,
        right=(
            False,
            False,
            False,
            False,
            True,
            True,
            True,
            True,
            True,
            True,
            False,
            False,
            False,
            False,
        ),
    )


def _provider_cells(
    outcome: EndpointOutcome,
    url_w: int,
    client_w: int,
    use_color: bool,
) -> list[str]:
    stats = outcome.stats
    ok = stats.n_ok > 0
    attempted = stats.n_ok + stats.n_fail
    hist = _hist_counts(stats.histogram) if ok else "—"
    return [
        _name_cell(outcome, use_color),
        _status_cell(outcome, use_color),
        _clip(outcome.endpoint.display_url, url_w).rstrip(),
        _clip(outcome.client or "—", client_w).rstrip(),
        f"{stats.n_ok}/{attempted}",
        _pct(stats.error_rate),
        str(assess_reliability(outcome).score),
        _cell_ms(stats.p95_ms),
        _cell_head(outcome).strip(),
        _cell_lag(outcome).strip(),
        _cell_fresh(outcome).strip(),
        _cell_agree(outcome).strip(),
        hist,
        _row_note(outcome),
    ]


def _provider_verbose_lines(outcome: EndpointOutcome, name_w: int) -> list[str]:
    indent = " " * (2 + name_w + 4)
    lines = [f"  {outcome.endpoint.name}"]
    if outcome.warmup:
        lines.append(f"{indent}warmup")
        lines.extend(_sample_lines(outcome.warmup, indent))
    lines.append(f"{indent}samples")
    lines.extend(_sample_lines(outcome.samples, indent))
    return lines


def _sample_lines(hits: tuple, indent: str) -> list[str]:
    lines: list[str] = []
    for i, hit in enumerate(hits, start=1):
        tag = f"  {hit.method}" if hit.method else ""
        if hit.ok and hit.latency_ms is not None:
            extra = _sample_timing_suffix(hit)
            lines.append(f"{indent}  {i:>3}  {hit.latency_ms:.1f}ms{extra}{tag}")
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
    if result.batch > 0:
        supported = [
            o.endpoint.name
            for o in ranked
            if o.batch is not None and o.batch.supported
        ]
        rejected = [
            o
            for o in ranked
            if o.batch is not None
            and not o.batch.supported
            and o.batch.error_class not in _BATCH_SKIP
        ]
        method = next(
            (o.batch.method for o in ranked if o.batch is not None),
            result.method,
        )
        lines.append(
            f"  batch ({method} × {result.batch})  "
            f"{len(supported)}/{total} supported"
        )
        if rejected:
            bits = []
            for outcome in rejected:
                cls = (
                    outcome.batch.error_class
                    if outcome.batch is not None
                    else "error"
                )
                bits.append(f"{outcome.endpoint.name} ({cls})")
            lines.append(f"  missed     {', '.join(bits)}")
    return lines


def _ranking_entry(row: RankedPlace, rank_by: str, verdict: Verdict) -> dict[str, Any]:
    outcome = row.outcome
    stats = outcome.stats
    rel = assess_reliability(outcome)
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
        "score": rel.score,
        "reliability": rel.as_dict(),
        "verdict": verdict.as_dict(),
        "freshness": _freshness_json(outcome),
        "consistency": _consistency_json(outcome),
        "timing": _timing_summary_json(outcome.timing),
        "transport": _transport_summary_json(outcome.transport),
        "batch": _batch_summary_json(outcome.batch),
    }


def _provider_entry(row: RankedPlace, method: str, verdict: Verdict) -> dict[str, Any]:
    outcome = row.outcome
    stats = outcome.stats
    rps = (1000.0 / stats.mean_ms) if stats.mean_ms else None
    return {
        "name": outcome.endpoint.name,
        "url": outcome.endpoint.display_url,
        "id": outcome.endpoint.url_id,
        "rank": row.rank,
        "similar": row.similar,
        "reliable": row.reliable,
        "ok": stats.n_ok > 0,
        "client": outcome.client,
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
        "reliability": assess_reliability(outcome).as_dict(),
        "verdict": verdict.as_dict(),
        "capability": {
            "method": method,
            "responded": stats.n_ok > 0,
        },
        "freshness": _freshness_json(outcome),
        "consistency": _consistency_json(outcome),
        "tags": [_tag_entry(outcome.endpoint.name, snap) for snap in outcome.tags],
        "phases": _phases_json(outcome),
        "timing": _timing_summary_json(outcome.timing),
        "transport": _transport_summary_json(outcome.transport),
        "batch": _batch_summary_json(outcome.batch),
        "last_error": _last_error(outcome) or None,
        "warmup": [_hit_entry(hit) for hit in outcome.warmup],
        "samples": [_hit_entry(hit) for hit in outcome.samples],
    }


def _hit_entry(hit: Any) -> dict[str, Any]:
    return {
        "ok": hit.ok,
        "reachable": hit.reachable,
        "latency_ms": hit.latency_ms,
        "method": hit.method,
        "http_version": hit.http_version,
        "encoding": hit.encoding,
        "bytes_out": hit.bytes_out,
        "bytes_in": hit.bytes_in,
        "error": hit.error,
        "error_class": hit.error_class,
        "attempts": hit.attempts,
        "timing": _hit_timing_json(hit.timing),
    }


def _sample_timing_suffix(hit: Any) -> str:
    timing = hit.timing
    if timing is None:
        return ""
    return (
        f"  hs={_fmt_ms(timing.handshake_ms())}  "
        f"srv={_fmt_ms(timing.server_ms)}  "
        f"pay={_fmt_ms(timing.payload_ms())}"
    )


def _fmt_ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}ms"


def _timing_summary_json(summary: Any) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "handshake": _timing_phase_json(summary.handshake),
        "server": _timing_phase_json(summary.server),
        "payload": _timing_phase_json(summary.payload),
        "dns": _timing_phase_json(summary.dns),
        "tcp": _timing_phase_json(summary.tcp),
        "tls": _timing_phase_json(summary.tls),
        "body": _timing_phase_json(summary.body),
        "parse": _timing_phase_json(summary.parse),
    }


def _transport_summary_json(summary: Any) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "http_version": summary.http_version,
        "encoding": summary.encoding,
        "n": summary.n,
        "bytes_out_mean": summary.bytes_out_mean,
        "bytes_in_mean": summary.bytes_in_mean,
        "bytes_in_p50": summary.bytes_in_p50,
        "bytes_in_p95": summary.bytes_in_p95,
    }


def _timing_phase_json(stats: Any) -> dict[str, Any]:
    return {
        "n": stats.n,
        "mean_ms": stats.mean_ms,
        "p50_ms": stats.p50_ms,
        "p95_ms": stats.p95_ms,
        "p99_ms": stats.p99_ms,
    }


def _hit_timing_json(timing: Any) -> dict[str, Any] | None:
    if timing is None:
        return None
    return {
        "dns_ms": timing.dns_ms,
        "tcp_ms": timing.tcp_ms,
        "tls_ms": timing.tls_ms,
        "server_ms": timing.server_ms,
        "body_ms": timing.body_ms,
        "parse_ms": timing.parse_ms,
        "handshake_ms": timing.handshake_ms(),
        "payload_ms": timing.payload_ms(),
    }


def _freshness_json(outcome: EndpointOutcome) -> dict[str, Any] | None:
    fresh = outcome.freshness
    if fresh is None:
        return None
    return {
        "height": fresh.height,
        "height_hex": fresh.height_hex,
        "lag_blocks": fresh.lag_blocks,
        "lag_s": fresh.lag_s,
        "verdict": fresh.verdict,
        "cohort_height": fresh.cohort_height,
    }


def _consistency_json(outcome: EndpointOutcome) -> dict[str, Any] | None:
    cons = outcome.consistency
    if cons is None:
        return None
    return {
        "hash": cons.hash,
        "number": cons.number,
        "verdict": cons.verdict,
        "pin_height": cons.pin_height,
        "canonical_hash": cons.canonical_hash,
    }


def _burst_mode_suffix(result: RunResult) -> str:
    extra = ""
    if result.burst > 0:
        extra += f"  ·  burst={result.burst}"
    if result.rps > 0:
        extra += f"  ·  rps={result.rps:g}"
    return extra


def _batch_mode_suffix(result: RunResult) -> str:
    if result.batch <= 0:
        return ""
    return f"  ·  batch={result.batch}"


def _batch_summary_json(summary: Any) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "size": summary.size,
        "method": summary.method,
        "supported": summary.supported,
        "partial": summary.partial,
        "batch_ms": summary.batch_ms,
        "serial_ms": summary.serial_ms,
        "ratio": summary.ratio,
        "n_ok": summary.n_ok,
        "n_fail": summary.n_fail,
        "error": summary.error,
        "error_class": summary.error_class,
    }


def _rps_label(rps: float) -> str:
    if rps <= 0:
        return "uncapped steady"
    return f"cap {rps:g}/s"


def _rate_limit_n(stats: Any) -> int:
    return dict(stats.by_class).get("rate_limit", 0)


def _tag_rate_limit_n(outcome: EndpointOutcome) -> int:
    return sum(1 for snap in outcome.tags if snap.skip_reason == "rate_limit")


def _rate_limit_cell(stats: Any, outcome: EndpointOutcome, *, tags: bool) -> str:
    timed = _rate_limit_n(stats)
    extra = _tag_rate_limit_n(outcome) if tags else 0
    if extra:
        return f"{timed}  tags={extra}"
    return str(timed)


def _phase_json(stats: Any) -> dict[str, Any] | None:
    if stats is None:
        return None
    return {
        "n_ok": stats.n_ok,
        "n_fail": stats.n_fail,
        "error_rate": stats.error_rate,
        "mean_ms": stats.mean_ms,
        "p95_ms": stats.p95_ms,
        "rps": (1000.0 / stats.mean_ms) if stats.mean_ms else None,
        "by_class": dict(stats.by_class),
        "rate_limit": _rate_limit_n(stats),
    }


def _phases_json(outcome: EndpointOutcome) -> dict[str, Any] | None:
    if outcome.burst_stats is None and outcome.steady_stats is None:
        return None
    return {
        "burst": _phase_json(outcome.burst_stats),
        "steady": _phase_json(outcome.steady_stats),
    }


def _burst_lines(
    result: RunResult, name_w: int, use_color: bool
) -> list[str]:
    rows: list[list[str]] = []
    for outcome in result.outcomes:
        name = _name_cell(outcome, use_color)
        phases: list[tuple[str, Any]] = []
        if outcome.burst_stats is not None:
            phases.append(("burst", outcome.burst_stats))
        if outcome.steady_stats is not None:
            phases.append(("steady", outcome.steady_stats))
        if not phases:
            cell = "—"
            extra = _tag_rate_limit_n(outcome)
            if extra:
                cell = f"0  tags={extra}"
            rows.append([name, "—", "—", "—", "—", "—", cell])
            continue
        first = True
        for phase, stats in phases:
            attempted = stats.n_ok + stats.n_fail
            n = f"{stats.n_ok}/{attempted}"
            rps = "—" if not stats.mean_ms else f"{1000.0 / stats.mean_ms:.1f}"
            cell = _rate_limit_cell(stats, outcome, tags=first)
            first = False
            rows.append(
                [
                    name,
                    phase,
                    n,
                    _pct(stats.error_rate),
                    _cell_ms(stats.p95_ms),
                    rps,
                    cell,
                ]
            )
    return _grid(
        ["endpoint", "phase", "n", "err", "p95", "rps", "rate_limit"],
        rows,
        right=(False, False, True, True, True, True, False),
    )


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
