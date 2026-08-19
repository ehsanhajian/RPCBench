"""Human-readable CLI report. Latency and ranking only — no security findings."""

from __future__ import annotations

import os
import sys

from rpcbench.run import EndpointOutcome, RunResult

_GREEN = "32"
_RED = "31"
_BOLD = "1"


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


def rank_outcomes(result: RunResult) -> tuple[EndpointOutcome, ...]:
    """Successful endpoints by mean latency, then failures in config order."""

    def key(item: tuple[int, EndpointOutcome]) -> tuple[int, float, int]:
        index, outcome = item
        stats = outcome.stats
        if stats.n_ok == 0 or stats.mean_ms is None:
            return (1, 0.0, index)
        return (0, stats.mean_ms, index)

    indexed = list(enumerate(result.outcomes))
    return tuple(outcome for _, outcome in sorted(indexed, key=key))


def format_run(
    result: RunResult,
    *,
    verbose: bool = False,
    color: bool | None = None,
) -> str:
    use_color = color_enabled(color)
    ranked = rank_outcomes(result)
    ok_rows = [o for o in ranked if o.stats.n_ok]
    fail_rows = [o for o in ranked if not o.stats.n_ok]
    params = f" {list(result.params)}" if result.params else ""
    lines = [
        "RPCBench",
        "=" * 72,
        f"Method    {result.method}{params}",
        f"Samples   {result.samples} after {result.warmup} warmup  ·  "
        f"Timeout {result.timeout:g}s  ·  "
        f"Budget {result.budget} ({result.budget_remaining} left)",
        "",
        "Summary",
    ]
    lines.extend(_summary_lines(ok_rows, fail_rows, len(result.outcomes), use_color))
    lines.extend(["", "Ranking  (by mean latency; failed last)"])
    name_w = max((len(o.endpoint.name) for o in ranked), default=4)
    rank_n = 0
    for outcome in ranked:
        if outcome.stats.n_ok:
            rank_n += 1
            mark = f"{rank_n:>3}"
        else:
            mark = "  —"
        lines.append(_ranking_line(outcome, mark, name_w, use_color))
    lines.extend(["", "Providers"])
    for outcome in ranked:
        lines.extend(_provider_lines(outcome, name_w, verbose, use_color))
    lines.extend(["", "Capabilities"])
    lines.extend(_capability_lines(result, ranked))
    lines.append("")
    lines.append(
        f"{len(ok_rows)} ok  {len(fail_rows)} failed  ·  warmup excluded  ·  "
        "err=failed/attempted  ·  min/mean/max and p50/p95/p99 of successful samples"
    )
    return "\n".join(lines) + "\n"


def _summary_lines(
    ok_rows: list[EndpointOutcome],
    fail_rows: list[EndpointOutcome],
    total: int,
    use_color: bool,
) -> list[str]:
    lines: list[str] = []
    if ok_rows:
        fastest = ok_rows[0]
        stats = fastest.stats
        name = _paint(fastest.endpoint.name, _BOLD, _GREEN, enabled=use_color)
        lines.append(
            f"  Fastest  {name}  mean={stats.mean_ms:.1f}ms  "
            f"p95={stats.p95_ms:.1f}ms  err={_pct(stats.error_rate)}"
        )
    else:
        lines.append("  Fastest  none  (all endpoints failed)")
    if fail_rows:
        names = ", ".join(o.endpoint.name for o in fail_rows)
        lines.append(f"  Failed   {len(fail_rows)}/{total}    {names}")
    else:
        lines.append(f"  Failed   0/{total}")
    return lines


def _ranking_line(
    outcome: EndpointOutcome, mark: str, name_w: int, use_color: bool
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
        return (
            f"  {mark}  {name}  {status}  n={stats.n_ok}/{attempted}  {rate}"
            f"{classes}  mean={stats.mean_ms:.1f}ms  p95={stats.p95_ms:.1f}ms"
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
            f"mean={stats.mean_ms:.1f}ms  max={stats.max_ms:.1f}ms"
        )
        lines.append(
            f"{indent}p50={stats.p50_ms:.1f}ms  p95={stats.p95_ms:.1f}ms  "
            f"p99={stats.p99_ms:.1f}ms  (n={stats.n_ok})"
        )
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
        if hit.ok and hit.latency_ms is not None:
            lines.append(f"{indent}  {i:>3}  {hit.latency_ms:.1f}ms")
        else:
            cls = hit.error_class or "error"
            msg = hit.error or ""
            lat = f"{hit.latency_ms:.1f}ms  " if hit.latency_ms is not None else ""
            lines.append(f"{indent}  {i:>3}  {lat}{cls}  {msg}".rstrip())
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
