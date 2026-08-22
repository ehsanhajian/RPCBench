"""Timed JSON-RPC run: latency stats, percentiles, and error rates."""

from __future__ import annotations

import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from rpcbench.config import BenchConfig, Endpoint
from rpcbench.methods import CallSpec
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
MODE_PAIRED = "paired"
MODE_SEQUENTIAL = "sequential"
# Exclusive upper bounds; last bucket is ≥ the final edge. Shared with CLI/JSON/HTML.
HISTOGRAM_EDGES_MS: tuple[float, ...] = (50.0, 100.0, 250.0, 1000.0)
HISTOGRAM_LABELS: tuple[str, ...] = ("<50ms", "<100ms", "<250ms", "<1s", "≥1s")


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
    jitter_ms: float | None
    histogram: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class EndpointOutcome:
    endpoint: Endpoint
    warmup: tuple[ProbeResult, ...]
    samples: tuple[ProbeResult, ...]
    stats: LatencyStats
    by_method: tuple[tuple[str, LatencyStats], ...] = ()


@dataclass(frozen=True)
class PairRecord:
    index: int
    kind: str
    method: str
    bodies: tuple[tuple[str, str | None], ...]


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
    mode: str = MODE_PAIRED
    seed: int = 0
    sequence_id: str = ""
    pairs: tuple[PairRecord, ...] = ()
    concurrency: int = 0
    profile: str = "single"
    workload: tuple[CallSpec, ...] = ()


def percentile(samples: list[float], p: float) -> float:
    """Nearest-rank percentile. ``p`` is in (0, 1]; ``samples`` must be non-empty."""
    if not samples:
        raise ValueError("percentile needs at least 1 sample")
    if not 0 < p <= 1:
        raise ValueError("percentile p must be in (0, 1]")
    ordered = sorted(samples)
    rank = max(1, math.ceil(p * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def make_sequence_id(
    *,
    seed: int,
    method: str,
    params: list[object],
    warmup: int,
    samples: int,
    workload: list[object] | None = None,
) -> str:
    blob = json.dumps(
        {
            "seed": seed,
            "method": method,
            "params": params,
            "warmup": warmup,
            "samples": samples,
            **({"workload": workload} if workload else {}),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def sample_stddev(values: list[float]) -> float | None:
    """Sample standard deviation (Bessel). None unless there are at least 2 values."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(var)


def latency_histogram(values: list[float]) -> tuple[tuple[str, int], ...]:
    """Coarse exclusive-upper-bound buckets. Empty input still returns zero counts."""
    counts = [0] * len(HISTOGRAM_LABELS)
    for value in values:
        placed = False
        for i, edge in enumerate(HISTOGRAM_EDGES_MS):
            if value < edge:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    return tuple(zip(HISTOGRAM_LABELS, counts, strict=True))


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
            jitter_ms=None,
            histogram=latency_histogram([]),
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
        jitter_ms=sample_stddev(ok),
        histogram=latency_histogram(ok),
    )


def expand_steps(
    workload: tuple[CallSpec, ...], warmup: int, samples: int
) -> list[tuple[str, int, CallSpec]]:
    """Warmup rounds of the mix, then timed rounds. Index is per-kind."""
    steps: list[tuple[str, int, CallSpec]] = []
    warm_i = 0
    for _ in range(warmup):
        for spec in workload:
            steps.append(("warmup", warm_i, spec))
            warm_i += 1
    sample_i = 0
    for _ in range(samples):
        for spec in workload:
            steps.append(("sample", sample_i, spec))
            sample_i += 1
    return steps


def _tag(hit: ProbeResult, method: str) -> ProbeResult:
    return hit if hit.method == method else replace(hit, method=method)


def _by_method(
    samples: tuple[ProbeResult, ...], workload: tuple[CallSpec, ...]
) -> tuple[tuple[str, LatencyStats], ...]:
    if not workload:
        return ()
    buckets: dict[str, list[ProbeResult]] = {spec.name: [] for spec in workload}
    method_to_name = {spec.method: spec.name for spec in workload}
    for hit in samples:
        name = method_to_name.get(hit.method or "")
        if name is None:
            continue
        buckets[name].append(hit)
    return tuple((name, summarize(tuple(buckets[name]))) for name in buckets)


def _workload_blob(workload: tuple[CallSpec, ...]) -> list[dict[str, object]]:
    return [
        {"name": spec.name, "method": spec.method, "params": list(spec.params)}
        for spec in workload
    ]


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
    mode: str = MODE_PAIRED,
    seed: int = 0,
    concurrency: int = 0,
    client=None,
    workload: tuple[CallSpec, ...] | None = None,
    profile: str = "single",
) -> RunResult:
    if samples < 1:
        raise ValueError("samples must be at least 1")
    if warmup < 0:
        raise ValueError("warmup must be >= 0")
    if max_duration < 0:
        raise ValueError("max_duration must be >= 0")
    if concurrency < 0:
        raise ValueError("concurrency must be >= 0")
    if mode not in {MODE_PAIRED, MODE_SEQUENTIAL}:
        raise ValueError("mode must be paired or sequential")
    rpc_params = list(params or [])
    steps = workload or (CallSpec("head", method, tuple(rpc_params)),)
    purse = RequestBudget(budget)
    deadline = None if max_duration <= 0 else time.monotonic() + max_duration
    seq_id = make_sequence_id(
        seed=seed,
        method=method,
        params=rpc_params,
        warmup=warmup,
        samples=samples,
        workload=_workload_blob(steps) if len(steps) > 1 else None,
    )
    if mode == MODE_SEQUENTIAL:
        outcomes, pairs = _run_sequential(
            config,
            workload=steps,
            samples=samples,
            warmup=warmup,
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            client=client,
        )
    else:
        outcomes, pairs = _run_paired(
            config,
            workload=steps,
            samples=samples,
            warmup=warmup,
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            concurrency=concurrency,
            client=client,
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
        mode=mode,
        seed=seed,
        sequence_id=seq_id,
        pairs=tuple(pairs),
        concurrency=concurrency,
        profile=profile,
        workload=steps,
    )


def _expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _skipped(error_class: str, error: str, method: str | None = None) -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=False,
        latency_ms=None,
        result=None,
        error=error,
        error_class=error_class,
        attempts=0,
        method=method,
    )


def _hit(
    endpoint: Endpoint,
    *,
    spec: CallSpec,
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    client,
) -> ProbeResult:
    if _expired(deadline):
        return _skipped("duration", "max duration exceeded", spec.method)
    return _tag(
        probe(
            endpoint.url,
            spec.method,
            params=list(spec.params),
            timeout=timeout,
            retries=0,
            budget=budget,
            client=client,
            headers=endpoint.headers,
        ),
        spec.method,
    )


def _finish_outcome(
    endpoint: Endpoint,
    warmup_hits: tuple[ProbeResult, ...],
    measured: tuple[ProbeResult, ...],
    workload: tuple[CallSpec, ...],
) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=endpoint,
        warmup=warmup_hits,
        samples=measured,
        stats=summarize(measured),
        by_method=_by_method(measured, workload),
    )


def _run_sequential(
    config: BenchConfig,
    *,
    workload: tuple[CallSpec, ...],
    samples: int,
    warmup: int,
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    client,
) -> tuple[list[EndpointOutcome], list[PairRecord]]:
    outcomes: list[EndpointOutcome] = []
    for endpoint in config.endpoints:
        outcomes.append(
            _run_one(
                endpoint,
                workload=workload,
                samples=samples,
                warmup=warmup,
                timeout=timeout,
                budget=budget,
                deadline=deadline,
                client=client,
            )
        )
    return outcomes, []


def _run_paired(
    config: BenchConfig,
    *,
    workload: tuple[CallSpec, ...],
    samples: int,
    warmup: int,
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    concurrency: int,
    client,
) -> tuple[list[EndpointOutcome], list[PairRecord]]:
    endpoints = list(config.endpoints)
    warmups: dict[str, list[ProbeResult]] = {ep.name: [] for ep in endpoints}
    measured: dict[str, list[ProbeResult]] = {ep.name: [] for ep in endpoints}
    pairs: list[PairRecord] = []
    plan = expand_steps(workload, warmup, samples)
    n = max(1, len(endpoints))
    workers = n if concurrency <= 0 else max(1, min(concurrency, n))

    def fire(endpoint: Endpoint, spec: CallSpec) -> ProbeResult:
        return _tag(
            probe(
                endpoint.url,
                spec.method,
                params=list(spec.params),
                timeout=timeout,
                retries=0,
                budget=budget,
                client=client,
                headers=endpoint.headers,
            ),
            spec.method,
        )

    for kind, index, spec in plan:
        if _expired(deadline):
            miss = _skipped("duration", "max duration exceeded", spec.method)
            for endpoint in endpoints:
                if kind == "warmup":
                    warmups[endpoint.name].append(miss)
                else:
                    measured[endpoint.name].append(miss)
            if kind == "sample":
                pairs.append(
                    PairRecord(
                        index=index,
                        kind=kind,
                        method=spec.method,
                        bodies=tuple((ep.name, None) for ep in endpoints),
                    )
                )
            continue
        hits: dict[str, ProbeResult] = {}
        if len(endpoints) == 1:
            hits[endpoints[0].name] = fire(endpoints[0], spec)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {ep.name: pool.submit(fire, ep, spec) for ep in endpoints}
                for name, fut in futs.items():
                    hits[name] = fut.result()
        for endpoint in endpoints:
            hit = hits[endpoint.name]
            if kind == "warmup":
                warmups[endpoint.name].append(hit)
            else:
                measured[endpoint.name].append(hit)
        if kind == "sample":
            pairs.append(
                PairRecord(
                    index=index,
                    kind=kind,
                    method=spec.method,
                    bodies=tuple(
                        (ep.name, hits[ep.name].body_hash) for ep in endpoints
                    ),
                )
            )
    outcomes = [
        _finish_outcome(
            endpoint,
            tuple(warmups[endpoint.name]),
            tuple(measured[endpoint.name]),
            workload,
        )
        for endpoint in endpoints
    ]
    return outcomes, pairs


def _run_one(
    endpoint: Endpoint,
    *,
    workload: tuple[CallSpec, ...],
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
    plan = expand_steps(workload, warmup, samples)

    if _expired(deadline):
        first = workload[0].method if workload else None
        measured.append(_skipped("duration", "max duration exceeded", first))
        return _finish_outcome(endpoint, (), tuple(measured), workload)
    for kind, _index, spec in plan:
        if stop:
            break
        hit = _hit(
            endpoint,
            spec=spec,
            timeout=timeout,
            budget=budget,
            deadline=deadline,
            client=client,
        )
        if kind == "warmup":
            warmup_hits.append(hit)
        else:
            measured.append(hit)
        if hit.error_class in _STOP_CLASSES:
            stop = True
            if kind == "warmup" and not measured:
                measured.append(hit)
    return _finish_outcome(endpoint, tuple(warmup_hits), tuple(measured), workload)
