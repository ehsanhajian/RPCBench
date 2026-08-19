"""Timed JSON-RPC run: latency stats, percentiles, and error rates."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from rpcbench.config import BenchConfig, Endpoint
from rpcbench.rpc import ProbeResult, RequestBudget, probe


_CLASS_ORDER = (
    "timeout",
    "connection",
    "http_4xx",
    "http_5xx",
    "jsonrpc",
    "malformed",
    "invalid_url",
    "budget",
    "duration",
)
_STOP_CLASSES = {"invalid_url", "budget", "duration"}


@dataclass(frozen=True)
class LatencyStats:
    n_ok: int
    n_fail: int
    error_rate: float | None
    by_class: tuple[tuple[str, int], ...]
    min_ms: float | None
    mean_ms: float | None
    max_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None


@dataclass(frozen=True)
class EndpointOutcome:
    endpoint: Endpoint
    warmup: tuple[ProbeResult, ...]
    samples: tuple[ProbeResult, ...]
    stats: LatencyStats


@dataclass(frozen=True)
class RunResult:
    method: str
    params: tuple[object, ...]
    samples: int
    warmup: int
    timeout: float
    budget: int
    outcomes: tuple[EndpointOutcome, ...]
    budget_remaining: int


def percentile(samples: list[float], p: float) -> float:
    """Nearest-rank percentile. ``p`` is in (0, 1]; ``samples`` must be non-empty."""
    if not samples:
        raise ValueError("percentile needs at least one sample")
    if not 0 < p <= 1:
        raise ValueError("percentile p must be in (0, 1]")
    ordered = sorted(samples)
    rank = max(1, math.ceil(p * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _count_classes(samples: tuple[ProbeResult, ...]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for sample in samples:
        if sample.ok or not sample.error_class:
            continue
        counts[sample.error_class] = counts.get(sample.error_class, 0) + 1
    rank = {name: i for i, name in enumerate(_CLASS_ORDER)}
    items = sorted(counts.items(), key=lambda kv: (rank.get(kv[0], 99), kv[0]))
    return tuple(items)


def summarize(samples: tuple[ProbeResult, ...]) -> LatencyStats:
    ok = [s.latency_ms for s in samples if s.ok and s.latency_ms is not None]
    n_fail = sum(1 for s in samples if not s.ok)
    attempted = len(ok) + n_fail
    error_rate = (n_fail / attempted) if attempted else None
    by_class = _count_classes(samples)
    if not ok:
        return LatencyStats(
            n_ok=0,
            n_fail=n_fail,
            error_rate=error_rate,
            by_class=by_class,
            min_ms=None,
            mean_ms=None,
            max_ms=None,
            p50_ms=None,
            p95_ms=None,
            p99_ms=None,
        )
    return LatencyStats(
        n_ok=len(ok),
        n_fail=n_fail,
        error_rate=error_rate,
        by_class=by_class,
        min_ms=min(ok),
        mean_ms=sum(ok) / len(ok),
        max_ms=max(ok),
        p50_ms=percentile(ok, 0.50),
        p95_ms=percentile(ok, 0.95),
        p99_ms=percentile(ok, 0.99),
    )


def run_endpoints(
    config: BenchConfig,
    *,
    method: str = "eth_blockNumber",
    params: list[object] | None = None,
    samples: int = 1,
    warmup: int = 0,
    timeout: float = 10.0,
    budget: int = 32,
    max_duration: float = 0.0,
    client=None,
) -> RunResult:
    if samples < 1:
        raise ValueError("samples must be at least 1")
    if warmup < 0:
        raise ValueError("warmup must be >= 0")
    if max_duration < 0:
        raise ValueError("max_duration must be >= 0")
    purse = RequestBudget(budget)
    rpc_params = list(params or [])
    deadline = None if max_duration <= 0 else time.monotonic() + max_duration
    outcomes: list[EndpointOutcome] = []
    for endpoint in config.endpoints:
        outcomes.append(
            _run_one(
                endpoint,
                method=method,
                params=rpc_params,
                samples=samples,
                warmup=warmup,
                timeout=timeout,
                budget=purse,
                deadline=deadline,
                client=client,
            )
        )
    return RunResult(
        method=method,
        params=tuple(rpc_params),
        samples=samples,
        warmup=warmup,
        timeout=timeout,
        budget=budget,
        outcomes=tuple(outcomes),
        budget_remaining=purse.remaining,
    )


def _expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _skipped(error_class: str, error: str) -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=False,
        latency_ms=None,
        result=None,
        error=error,
        error_class=error_class,
        attempts=0,
    )


def _run_one(
    endpoint: Endpoint,
    *,
    method: str,
    params: list[object],
    samples: int,
    warmup: int,
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    client,
) -> EndpointOutcome:
    warmup_hits: list[ProbeResult] = []
    measured: list[ProbeResult] = []
    stop = False

    def hit_once() -> ProbeResult:
        if _expired(deadline):
            return _skipped("duration", "max duration exceeded")
        return probe(
            endpoint.url,
            method,
            params=params,
            timeout=timeout,
            retries=0,
            budget=budget,
            client=client,
            headers=endpoint.headers,
        )

    if _expired(deadline):
        measured.append(_skipped("duration", "max duration exceeded"))
        return EndpointOutcome(
            endpoint=endpoint,
            warmup=(),
            samples=tuple(measured),
            stats=summarize(tuple(measured)),
        )
    for _ in range(warmup):
        hit = hit_once()
        warmup_hits.append(hit)
        if hit.error_class in _STOP_CLASSES:
            stop = True
            break
    if not stop:
        for _ in range(samples):
            hit = hit_once()
            measured.append(hit)
            if hit.error_class in _STOP_CLASSES:
                break
    elif not measured:
        last = warmup_hits[-1] if warmup_hits else None
        if last is not None and last.error_class in _STOP_CLASSES:
            measured.append(last)
    return EndpointOutcome(
        endpoint=endpoint,
        warmup=tuple(warmup_hits),
        samples=tuple(measured),
        stats=summarize(tuple(measured)),
    )

