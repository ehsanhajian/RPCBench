"""Bounded load shapes: flat, ramp, spike, soak. Extra read; not ranking."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from rpcbench.config import Endpoint
from rpcbench.methods import CallSpec
from rpcbench.rpc import ProbeResult, RequestBudget

SHAPE_NAMES = ("flat", "ramp", "spike", "soak")
WINDOW_S = 2.0


@dataclass(frozen=True)
class ShapeSpec:
    """Conservative defaults — short durations, hard request/concurrency caps."""

    name: str
    duration_s: float
    max_requests: int
    max_concurrency: int
    start_rps: float
    end_rps: float
    spike_rps: float = 0.0
    spike_start_s: float = 0.0
    spike_end_s: float = 0.0


# Public-RPC-safe: short windows, small caps (docs/METHODOLOGY.md).
SHAPE_SPECS: dict[str, ShapeSpec] = {
    "flat": ShapeSpec(
        name="flat",
        duration_s=8.0,
        max_requests=32,
        max_concurrency=2,
        start_rps=4.0,
        end_rps=4.0,
    ),
    "ramp": ShapeSpec(
        name="ramp",
        duration_s=12.0,
        max_requests=40,
        max_concurrency=2,
        start_rps=1.0,
        end_rps=6.0,
    ),
    "spike": ShapeSpec(
        name="spike",
        duration_s=10.0,
        max_requests=40,
        max_concurrency=4,
        start_rps=2.0,
        end_rps=2.0,
        spike_rps=8.0,
        spike_start_s=4.0,
        spike_end_s=6.0,
    ),
    "soak": ShapeSpec(
        name="soak",
        duration_s=20.0,
        max_requests=48,
        max_concurrency=2,
        start_rps=2.0,
        end_rps=2.0,
    ),
}


@dataclass(frozen=True)
class ShapeWindow:
    """One time bucket during a shape run."""

    t_s: float
    target_rps: float
    rps: float | None
    p95_ms: float | None
    error_rate: float | None
    n_ok: int
    n_fail: int


@dataclass(frozen=True)
class ShapeSummary:
    """Per-endpoint shape result. Not mixed into ranking."""

    shape: str
    method: str
    duration_s: float
    max_requests: int
    max_concurrency: int
    n_ok: int
    n_fail: int
    n: int
    rate_limit: int
    series: tuple[ShapeWindow, ...]
    error: str | None
    error_class: str | None
    skip: str | None = None


def normalize_shape(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text or text in {"off", "none", "0"}:
        return None
    if text not in SHAPE_SPECS:
        known = ", ".join(SHAPE_NAMES)
        raise ValueError(f"shape must be one of: {known} (got {raw!r})")
    return text


def target_rps_at(spec: ShapeSpec, elapsed_s: float) -> float:
    if spec.name == "spike":
        if spec.spike_start_s <= elapsed_s < spec.spike_end_s:
            return spec.spike_rps
        return spec.start_rps
    if spec.duration_s <= 0:
        return spec.end_rps
    frac = min(1.0, max(0.0, elapsed_s / spec.duration_s))
    return spec.start_rps + (spec.end_rps - spec.start_rps) * frac


def measure_shape(
    endpoint: Endpoint,
    *,
    spec: CallSpec,
    shape: str,
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    hit: Callable[..., ProbeResult],
) -> ShapeSummary:
    """Serial paced shape; target RPS follows the curve. Concurrency is a profile cap."""
    shape_spec = SHAPE_SPECS[shape]
    if _expired(deadline):
        return _skipped(shape_spec, spec.method, "duration", "max duration exceeded")
    started = time.monotonic()
    end_at = started + shape_spec.duration_s
    if deadline is not None:
        end_at = min(end_at, deadline)
    hits: list[tuple[float, ProbeResult]] = []
    last_start: float | None = None
    while len(hits) < shape_spec.max_requests:
        now = time.monotonic()
        if now >= end_at or _expired(deadline):
            break
        elapsed = now - started
        target = target_rps_at(shape_spec, elapsed)
        last_start = _pace(last_start, target)
        if time.monotonic() >= end_at or _expired(deadline):
            break
        t0 = time.monotonic()
        result = hit(
            endpoint,
            spec=spec,
            timeout=timeout,
            budget=budget,
            deadline=deadline,
        )
        hits.append((t0, result))

    if not hits:
        return _skipped(shape_spec, spec.method, "duration", "max duration exceeded")

    series = _bucket(hits, started, shape_spec)
    n_ok = sum(1 for _, h in hits if h.ok)
    n_fail = len(hits) - n_ok
    rate_limit = sum(1 for _, h in hits if h.error_class == "rate_limit")
    error = None
    error_class = None
    if n_ok == 0:
        miss = next((h for _, h in hits if h.error_class), None)
        if miss is not None:
            error = miss.error
            error_class = miss.error_class
    return ShapeSummary(
        shape=shape_spec.name,
        method=spec.method,
        duration_s=shape_spec.duration_s,
        max_requests=shape_spec.max_requests,
        max_concurrency=shape_spec.max_concurrency,
        n_ok=n_ok,
        n_fail=n_fail,
        n=len(hits),
        rate_limit=rate_limit,
        series=tuple(series),
        error=error,
        error_class=error_class,
    )


def shape_dict(summary: ShapeSummary) -> dict[str, Any]:
    return {
        "shape": summary.shape,
        "method": summary.method,
        "duration_s": summary.duration_s,
        "max_requests": summary.max_requests,
        "max_concurrency": summary.max_concurrency,
        "n_ok": summary.n_ok,
        "n_fail": summary.n_fail,
        "n": summary.n,
        "rate_limit": summary.rate_limit,
        "error": summary.error,
        "error_class": summary.error_class,
        "skip": summary.skip,
        "series": [
            {
                "t_s": win.t_s,
                "target_rps": win.target_rps,
                "rps": win.rps,
                "p95_ms": win.p95_ms,
                "error_rate": win.error_rate,
                "n_ok": win.n_ok,
                "n_fail": win.n_fail,
            }
            for win in summary.series
        ],
    }


def shape_status_label(summary: ShapeSummary | None) -> str:
    if summary is None:
        return "—"
    if summary.skip:
        return summary.skip
    if summary.n == 0:
        return "skip"
    if summary.n_ok == 0:
        return summary.error_class or "failed"
    if summary.rate_limit:
        return "rate-limited"
    return "ok"


def _bucket(
    hits: list[tuple[float, ProbeResult]],
    started: float,
    spec: ShapeSpec,
) -> list[ShapeWindow]:
    if not hits:
        return []
    last_t = max(t for t, _ in hits)
    span = max(WINDOW_S, last_t - started)
    n_windows = max(1, int(span / WINDOW_S) + 1)
    windows: list[ShapeWindow] = []
    for i in range(n_windows):
        lo = started + i * WINDOW_S
        hi = lo + WINDOW_S
        group = [h for t, h in hits if lo <= t < hi]
        t_mid = i * WINDOW_S
        target = target_rps_at(spec, t_mid)
        if not group:
            windows.append(
                ShapeWindow(
                    t_s=t_mid,
                    target_rps=target,
                    rps=None,
                    p95_ms=None,
                    error_rate=None,
                    n_ok=0,
                    n_fail=0,
                )
            )
            continue
        ok_lat = [h.latency_ms for h in group if h.ok and h.latency_ms is not None]
        n_ok = sum(1 for h in group if h.ok)
        n_fail = len(group) - n_ok
        rps = n_ok / WINDOW_S
        p95 = _percentile(ok_lat, 0.95) if ok_lat else None
        err = n_fail / len(group)
        windows.append(
            ShapeWindow(
                t_s=t_mid,
                target_rps=target,
                rps=rps,
                p95_ms=p95,
                error_rate=err,
                n_ok=n_ok,
                n_fail=n_fail,
            )
        )
    while len(windows) > 1 and windows[-1].n_ok == 0 and windows[-1].n_fail == 0:
        windows.pop()
    return windows


def _percentile(samples: list[float], p: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        raise ValueError("percentile needs samples")
    rank = max(1, int(round(p * len(ordered))))
    return ordered[min(rank, len(ordered)) - 1]


def _pace(last_start: float | None, rps: float) -> float:
    if rps <= 0:
        return time.monotonic()
    now = time.monotonic()
    if last_start is None:
        return now
    wait = last_start + (1.0 / rps) - now
    if wait > 0:
        time.sleep(wait)
        return time.monotonic()
    return time.monotonic()


def _expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _skipped(
    spec: ShapeSpec, method: str, error_class: str, error: str
) -> ShapeSummary:
    return ShapeSummary(
        shape=spec.name,
        method=method,
        duration_s=spec.duration_s,
        max_requests=spec.max_requests,
        max_concurrency=spec.max_concurrency,
        n_ok=0,
        n_fail=0,
        n=0,
        rate_limit=0,
        series=(),
        error=error,
        error_class=error_class,
        skip=error_class,
    )
