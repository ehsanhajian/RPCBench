"""Timed JSON-RPC run: latency stats, percentiles, and error rates."""

from __future__ import annotations

import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from rpcbench.config import BenchConfig, Endpoint
from rpcbench.consistency import (
    Consistency,
    assess_consistency,
    parse_block_hash,
    parse_block_number,
)
from rpcbench.freshness import (
    DEFAULT_BLOCK_TIME_S,
    DEFAULT_STALE_BLOCKS,
    Freshness,
    assess_freshness,
    block_time_for_chain,
    parse_block_height,
)
from rpcbench.methods import CallSpec
from rpcbench.rpc import ProbeResult, RequestBudget, probe
from rpcbench.tags import (
    BLOCK_TAGS,
    CLIENT_METHOD,
    TagSnapshot,
    client_from_hit,
    snapshots_from_hits,
)


_CLASS_ORDER = (
    "timeout",
    "connection",
    "rate_limit",
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
# Opt-in overlap of existing timed samples. Not an unbounded limit probe.
MAX_BURST = 8
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
    freshness: Freshness | None = None
    consistency: Consistency | None = None
    client: str | None = None
    tags: tuple[TagSnapshot, ...] = ()
    burst_stats: LatencyStats | None = None
    steady_stats: LatencyStats | None = None


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
    sample_budget: str = "standard"
    stale_blocks: int = DEFAULT_STALE_BLOCKS
    block_time_s: float = DEFAULT_BLOCK_TIME_S
    cohort_height: int | None = None
    pin_height: int | None = None
    canonical_hash: str | None = None
    burst: int = 0
    rps: float = 0.0


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
    sample_budget: str = "standard",
    stale_blocks: int = DEFAULT_STALE_BLOCKS,
    block_time_s: float | None = None,
    block_pin: int | None = None,
    burst: int = 0,
    rps: float = 0.0,
) -> RunResult:
    if samples < 1:
        raise ValueError("samples must be at least 1")
    if warmup < 0:
        raise ValueError("warmup must be >= 0")
    if max_duration < 0:
        raise ValueError("max_duration must be >= 0")
    if concurrency < 0:
        raise ValueError("concurrency must be >= 0")
    if burst < 0 or burst > MAX_BURST:
        raise ValueError(f"burst must be 0–{MAX_BURST}")
    if rps < 0:
        raise ValueError("rps must be >= 0")
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
            burst=burst,
            rps=rps,
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
            burst=burst,
            rps=rps,
            client=client,
        )
    extra_heads: dict[str, ProbeResult] = {}
    wave_concurrency = 1 if mode == MODE_SEQUENTIAL else concurrency
    if not any(spec.method == "eth_blockNumber" for spec in steps):
        extra_heads = _probe_wave(
            config,
            method="eth_blockNumber",
            params=[],
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            concurrency=wave_concurrency,
            client=client,
        )
    chain_id = _sample_chain_id(outcomes)
    resolved_time = block_time_for_chain(chain_id, block_time_s)
    heights = {
        outcome.endpoint.name: _head_height(outcome, method, extra_heads)
        for outcome in outcomes
    }
    judged = assess_freshness(
        heights, stale_blocks=stale_blocks, block_time_s=resolved_time
    )
    outcomes = [
        replace(outcome, freshness=judged[outcome.endpoint.name])
        for outcome in outcomes
    ]
    tip = next((row.cohort_height for row in judged.values()), None)
    pin = block_pin if block_pin is not None else tip
    extra_blocks: dict[str, ProbeResult] = {}
    if pin is not None:
        extra_blocks = _probe_wave(
            config,
            method="eth_getBlockByNumber",
            params=[hex(pin), False],
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            concurrency=wave_concurrency,
            client=client,
        )
    hashes = {
        outcome.endpoint.name: _block_hash(extra_blocks.get(outcome.endpoint.name))
        for outcome in outcomes
    }
    numbers = {
        outcome.endpoint.name: _block_number(extra_blocks.get(outcome.endpoint.name))
        for outcome in outcomes
    }
    agreed = assess_consistency(hashes, numbers=numbers, pin_height=pin)
    outcomes = [
        replace(outcome, consistency=agreed[outcome.endpoint.name])
        for outcome in outcomes
    ]
    canon = next((row.canonical_hash for row in agreed.values()), None)
    client_hits = _probe_wave(
        config,
        method=CLIENT_METHOD,
        params=[],
        timeout=timeout,
        budget=purse,
        deadline=deadline,
        concurrency=wave_concurrency,
        client=client,
    )
    tag_rows: dict[str, list[TagSnapshot]] = {
        outcome.endpoint.name: [] for outcome in outcomes
    }
    for tag in BLOCK_TAGS:
        hits = _probe_wave(
            config,
            method="eth_getBlockByNumber",
            params=[tag, False],
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            concurrency=wave_concurrency,
            client=client,
        )
        judged_tags = snapshots_from_hits(
            tag, hits, stale_blocks=stale_blocks, block_time_s=resolved_time
        )
        for name, snap in judged_tags.items():
            tag_rows[name].append(snap)
    outcomes = [
        replace(
            outcome,
            client=client_from_hit(client_hits.get(outcome.endpoint.name)),
            tags=tuple(tag_rows.get(outcome.endpoint.name, ())),
        )
        for outcome in outcomes
    ]
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
        sample_budget=sample_budget,
        stale_blocks=stale_blocks,
        block_time_s=resolved_time,
        cohort_height=tip,
        pin_height=pin,
        canonical_hash=canon,
        burst=min(burst, samples * len(steps)),
        rps=rps,
    )


def _sample_height(outcome: EndpointOutcome, run_method: str) -> int | None:
    for hit in outcome.samples:
        if not hit.ok:
            continue
        method = hit.method or run_method
        if method != "eth_blockNumber":
            continue
        height = parse_block_height(hit.result)
        if height is not None:
            return height
    return None


def _head_height(
    outcome: EndpointOutcome,
    run_method: str,
    extra: dict[str, ProbeResult],
) -> int | None:
    height = _sample_height(outcome, run_method)
    if height is not None:
        return height
    hit = extra.get(outcome.endpoint.name)
    if hit is None or not hit.ok:
        return None
    return parse_block_height(hit.result)


def _sample_chain_id(outcomes: list[EndpointOutcome]) -> int | None:
    for outcome in outcomes:
        for hit in outcome.samples:
            if not hit.ok:
                continue
            if (hit.method or "") != "eth_chainId":
                continue
            parsed = parse_block_height(hit.result)
            if parsed is not None:
                return parsed
    return None


def _block_hash(hit: ProbeResult | None) -> str | None:
    if hit is None or not hit.ok:
        return None
    return parse_block_hash(hit.result)


def _block_number(hit: ProbeResult | None) -> int | None:
    if hit is None or not hit.ok:
        return None
    return parse_block_number(hit.result)


def _probe_wave(
    config: BenchConfig,
    *,
    method: str,
    params: list[object],
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    concurrency: int,
    client,
) -> dict[str, ProbeResult]:
    endpoints = list(config.endpoints)
    hits: dict[str, ProbeResult] = {}
    if _expired(deadline):
        miss = _skipped("duration", "max duration exceeded", method)
        return {ep.name: miss for ep in endpoints}
    n = max(1, len(endpoints))
    workers = n if concurrency <= 0 else max(1, min(concurrency, n))

    def fire(endpoint: Endpoint) -> ProbeResult:
        return _tag(
            probe(
                endpoint.url,
                method,
                params=params,
                timeout=timeout,
                retries=0,
                budget=budget,
                client=client,
                headers=endpoint.headers,
            ),
            method,
        )

    if len(endpoints) == 1:
        hits[endpoints[0].name] = fire(endpoints[0])
        return hits
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {ep.name: pool.submit(fire, ep) for ep in endpoints}
        for name, fut in futs.items():
            hits[name] = fut.result()
    return hits


def _split_timed(
    plan: list[tuple[str, int, CallSpec]], burst: int
) -> tuple[
    list[tuple[str, int, CallSpec]],
    list[tuple[str, int, CallSpec]],
    list[tuple[str, int, CallSpec]],
]:
    warmup = [step for step in plan if step[0] == "warmup"]
    timed = [step for step in plan if step[0] != "warmup"]
    n = min(max(burst, 0), len(timed))
    return warmup, timed[:n], timed[n:]


def _pace(last_start: float | None, rps: float) -> float:
    if rps <= 0:
        return last_start if last_start is not None else 0.0
    now = time.monotonic()
    if last_start is None:
        return now
    wait = last_start + (1.0 / rps) - now
    if wait > 0:
        time.sleep(wait)
        return time.monotonic()
    return time.monotonic()


def _phase_stats(
    measured: tuple[ProbeResult, ...], burst: int
) -> tuple[LatencyStats | None, LatencyStats | None]:
    if burst <= 0 or not measured:
        return None, None
    n = min(burst, len(measured))
    burst_stats = summarize(measured[:n])
    rest = measured[n:]
    steady_stats = summarize(rest) if rest else None
    return burst_stats, steady_stats


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
    burst: int = 0,
) -> EndpointOutcome:
    burst_stats, steady_stats = _phase_stats(measured, burst)
    return EndpointOutcome(
        endpoint=endpoint,
        warmup=warmup_hits,
        samples=measured,
        stats=summarize(measured),
        by_method=_by_method(measured, workload),
        burst_stats=burst_stats,
        steady_stats=steady_stats,
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
    burst: int,
    rps: float,
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
                burst=burst,
                rps=rps,
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
    burst: int,
    rps: float,
    client,
) -> tuple[list[EndpointOutcome], list[PairRecord]]:
    endpoints = list(config.endpoints)
    warmups: dict[str, list[ProbeResult]] = {ep.name: [] for ep in endpoints}
    measured: dict[str, list[ProbeResult]] = {ep.name: [] for ep in endpoints}
    pairs: list[PairRecord] = []
    warmup_plan, burst_plan, steady_plan = _split_timed(
        expand_steps(workload, warmup, samples), burst
    )
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

    def record(kind: str, index: int, spec: CallSpec, hits: dict[str, ProbeResult]) -> None:
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

    def one_wave(kind: str, index: int, spec: CallSpec) -> None:
        if _expired(deadline):
            miss = _skipped("duration", "max duration exceeded", spec.method)
            record(kind, index, spec, {ep.name: miss for ep in endpoints})
            return
        hits: dict[str, ProbeResult] = {}
        if len(endpoints) == 1:
            hits[endpoints[0].name] = fire(endpoints[0], spec)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {ep.name: pool.submit(fire, ep, spec) for ep in endpoints}
                for name, fut in futs.items():
                    hits[name] = fut.result()
        record(kind, index, spec, hits)

    for kind, index, spec in warmup_plan:
        one_wave(kind, index, spec)

    if burst_plan:
        if _expired(deadline):
            miss_steps = burst_plan
            still: list[tuple[str, int, CallSpec]] = []
        else:
            miss_steps = []
            still = burst_plan
        if still:
            jobs = [
                (kind, index, spec, ep)
                for kind, index, spec in still
                for ep in endpoints
            ]
            workers_burst = max(1, len(jobs))
            with ThreadPoolExecutor(max_workers=workers_burst) as pool:
                futs = {
                    (index, ep.name): pool.submit(fire, ep, spec)
                    for kind, index, spec, ep in jobs
                }
                for kind, index, spec in still:
                    hits = {
                        ep.name: futs[(index, ep.name)].result() for ep in endpoints
                    }
                    record(kind, index, spec, hits)
        for kind, index, spec in miss_steps:
            miss = _skipped("duration", "max duration exceeded", spec.method)
            record(kind, index, spec, {ep.name: miss for ep in endpoints})

    last_start: float | None = None
    for kind, index, spec in steady_plan:
        last_start = _pace(last_start, rps)
        one_wave(kind, index, spec)

    outcomes = [
        _finish_outcome(
            endpoint,
            tuple(warmups[endpoint.name]),
            tuple(measured[endpoint.name]),
            workload,
            burst=len(burst_plan),
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
    burst: int,
    rps: float,
    client,
) -> EndpointOutcome:
    warmup_hits: list[ProbeResult] = []
    measured: list[ProbeResult] = []
    stop = False
    warmup_plan, burst_plan, steady_plan = _split_timed(
        expand_steps(workload, warmup, samples), burst
    )

    if _expired(deadline):
        first = workload[0].method if workload else None
        measured.append(_skipped("duration", "max duration exceeded", first))
        return _finish_outcome(endpoint, (), tuple(measured), workload, burst=0)
    for _kind, _index, spec in warmup_plan:
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
        warmup_hits.append(hit)
        if hit.error_class in _STOP_CLASSES:
            stop = True
            if not measured:
                measured.append(hit)
    if not stop and burst_plan:
        with ThreadPoolExecutor(max_workers=max(1, len(burst_plan))) as pool:
            futs = [
                pool.submit(
                    _hit,
                    endpoint,
                    spec=spec,
                    timeout=timeout,
                    budget=budget,
                    deadline=deadline,
                    client=client,
                )
                for _kind, _index, spec in burst_plan
            ]
            for fut in futs:
                hit = fut.result()
                measured.append(hit)
                if hit.error_class in _STOP_CLASSES:
                    stop = True
    last_start: float | None = None
    for _kind, _index, spec in steady_plan:
        if stop:
            break
        last_start = _pace(last_start, rps)
        hit = _hit(
            endpoint,
            spec=spec,
            timeout=timeout,
            budget=budget,
            deadline=deadline,
            client=client,
        )
        measured.append(hit)
        if hit.error_class in _STOP_CLASSES:
            stop = True
    return _finish_outcome(
        endpoint,
        tuple(warmup_hits),
        tuple(measured),
        workload,
        burst=len(burst_plan),
    )
