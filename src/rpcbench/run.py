"""Timed JSON-RPC run: latency stats, percentiles, and error rates."""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from rpcbench.archive import (
    ArchiveHit,
    archive_block,
    archive_params,
    hit_from_probe as archive_from_probe,
    skipped_archive,
)
from rpcbench.history import (
    HistoryHit,
    history_block,
    history_params,
    history_skip_reason,
    hit_from_probes as history_from_probes,
    latest_params,
    skipped_history,
)
from rpcbench.websocket import (
    DEFAULT_WEBSOCKET,
    MAX_WEBSOCKET,
    WebsocketHit,
    probe_websocket,
)
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
    cohort_height,
    parse_block_height,
)
from rpcbench.logs import (
    MAX_LOGS_RANGE,
    LogsRangeHit,
    family_skip_reason,
    hit_from_probe,
    logs_filter,
    logs_window,
    ranges_for,
    skipped_range,
)
from rpcbench.coverage import optional_skip_reason
from rpcbench.methods import CallSpec
from rpcbench.profile import (
    PayloadMeta,
    bind_workload,
    has_dynamic_source,
    hint_needs,
    payload_kind,
)
from rpcbench.rpc import (
    HTTP_1,
    HTTP_2,
    ProbeResult,
    RequestBudget,
    make_client,
    probe,
    probe_batch,
    reset_transport,
    set_transport,
)
from rpcbench.timing import CONN_KEEPALIVE, CONN_NEW
from rpcbench.family import benchmark_family, pin_block_params, resolve_block_time, tag_block_params
from rpcbench.watermark import FAMILY_EVM, git_sha as current_git_sha, utc_stamp, vantage_label
from rpcbench.tags import (
    TagSnapshot,
    client_from_hit,
    snapshots_from_hits,
)


def _submit(pool: ThreadPoolExecutor, fn, *args, **kwargs):
    """Run fn in a worker with this thread's contextvars (transport, …)."""
    ctx = contextvars.copy_context()

    def runner():
        return fn(*args, **kwargs)

    return pool.submit(ctx.run, runner)


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
# Opt-in JSON-RPC batch vs serial extra read. Small and bounded.
DEFAULT_BATCH = 3
MAX_BATCH = 8
# Opt-in overlapping HTTP POSTs vs serial extra read. Small and bounded.
DEFAULT_INFLIGHT = 4
MAX_INFLIGHT = 8
# Opt-in serial throughput extra read. Small and bounded.
DEFAULT_THROUGHPUT = 20
MAX_THROUGHPUT = 64
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
class PhaseStats:
    n: int
    mean_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None


@dataclass(frozen=True)
class TimingSummary:
    handshake: PhaseStats
    server: PhaseStats
    payload: PhaseStats
    dns: PhaseStats
    tcp: PhaseStats
    tls: PhaseStats
    body: PhaseStats
    parse: PhaseStats


@dataclass(frozen=True)
class TransportSummary:
    http_version: str | None
    encoding: str | None
    n: int
    bytes_out_mean: float | None
    bytes_in_mean: float | None
    bytes_in_p50: float | None
    bytes_in_p95: float | None


@dataclass(frozen=True)
class BatchSummary:
    """Batch POST vs N serial calls. Extra read; not mixed into ranking."""

    size: int
    method: str
    supported: bool
    partial: bool
    batch_ms: float | None
    serial_ms: float | None
    ratio: float | None
    n_ok: int
    n_fail: int
    error: str | None
    error_class: str | None


@dataclass(frozen=True)
class ConcurrentSummary:
    """N overlapping POSTs vs N serial. Extra read; not mixed into ranking."""

    size: int
    method: str
    concurrent_p50_ms: float | None
    concurrent_p95_ms: float | None
    serial_p50_ms: float | None
    serial_p95_ms: float | None
    concurrent_ms: float | None
    serial_ms: float | None
    ratio: float | None
    n_ok: int
    n_fail: int
    error: str | None
    error_class: str | None


@dataclass(frozen=True)
class ThroughputSummary:
    """Serial extra-read window. Successful req/s; not mixed into ranking."""

    size: int
    method: str
    rps: float | None
    duration_ms: float | None
    n_ok: int
    n_fail: int
    n: int
    rate_limit: int
    error: str | None
    error_class: str | None


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
    timing: TimingSummary | None = None
    transport: TransportSummary | None = None
    batch: BatchSummary | None = None
    inflight: ConcurrentSummary | None = None
    throughput: ThroughputSummary | None = None
    logs_range: tuple[LogsRangeHit, ...] = ()
    archive: ArchiveHit | None = None
    history: HistoryHit | None = None
    websocket: WebsocketHit | None = None


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
    connection: str = CONN_KEEPALIVE
    http: str = HTTP_1
    batch: int = 0
    inflight: int = 0
    throughput: int = 0
    logs_range: int = 0
    simulate: bool = False
    archive: bool = False
    lookback: int = 0
    websocket: float = 0.0
    family: str = FAMILY_EVM
    git_sha: str | None = None
    started_at: str | None = None
    vantage: str | None = None
    profile_notes: str | None = None
    payload: PayloadMeta | None = None


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


def _timing_phase_stats(values: list[float]) -> PhaseStats:
    if not values:
        return PhaseStats(n=0, mean_ms=None, p50_ms=None, p95_ms=None, p99_ms=None)
    return PhaseStats(
        n=len(values),
        mean_ms=sum(values) / len(values),
        p50_ms=percentile(values, 0.50),
        p95_ms=percentile(values, 0.95),
        p99_ms=percentile(values, 0.99),
    )


def summarize_timing(samples: tuple[ProbeResult, ...]) -> TimingSummary | None:
    timed = [hit.timing for hit in samples if hit.ok and hit.timing is not None]
    if not timed:
        return None

    def attr(name: str) -> list[float]:
        return [getattr(row, name) for row in timed if getattr(row, name) is not None]

    return TimingSummary(
        handshake=_timing_phase_stats([row.handshake_ms() for row in timed]),
        server=_timing_phase_stats(attr("server_ms")),
        payload=_timing_phase_stats(
            [row.payload_ms() for row in timed if row.payload_ms() is not None]
        ),
        dns=_timing_phase_stats(attr("dns_ms")),
        tcp=_timing_phase_stats(attr("tcp_ms")),
        tls=_timing_phase_stats(attr("tls_ms")),
        body=_timing_phase_stats(attr("body_ms")),
        parse=_timing_phase_stats(attr("parse_ms")),
    )


def _most_common(values: list[str]) -> str | None:
    if not values:
        return None
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def summarize_transport(samples: tuple[ProbeResult, ...]) -> TransportSummary | None:
    measured = [
        hit
        for hit in samples
        if hit.http_version or hit.bytes_in is not None or hit.bytes_out is not None
    ]
    if not measured:
        return None
    hits = [hit for hit in measured if hit.ok] or measured
    incoming = [float(hit.bytes_in) for hit in hits if hit.bytes_in is not None]
    outgoing = [float(hit.bytes_out) for hit in hits if hit.bytes_out is not None]
    return TransportSummary(
        http_version=_most_common(
            [hit.http_version for hit in hits if hit.http_version]
        ),
        encoding=_most_common([hit.encoding for hit in hits if hit.encoding]),
        n=len(hits),
        bytes_out_mean=(sum(outgoing) / len(outgoing)) if outgoing else None,
        bytes_in_mean=(sum(incoming) / len(incoming)) if incoming else None,
        bytes_in_p50=percentile(incoming, 0.50) if incoming else None,
        bytes_in_p95=percentile(incoming, 0.95) if incoming else None,
    )


def expand_steps(
    workload: tuple[CallSpec, ...], warmup: int, samples: int
) -> list[tuple[str, int, CallSpec]]:
    """Warmup rounds of the weighted mix, then timed rounds. Index is per-kind."""
    steps: list[tuple[str, int, CallSpec]] = []
    warm_i = 0
    for _ in range(warmup):
        for spec in workload:
            for _copy in range(max(1, spec.weight)):
                steps.append(("warmup", warm_i, spec))
                warm_i += 1
    sample_i = 0
    for _ in range(samples):
        for spec in workload:
            for _copy in range(max(1, spec.weight)):
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
    rows: list[dict[str, object]] = []
    for spec in workload:
        row: dict[str, object] = {
            "name": spec.name,
            "method": spec.method,
            "params": list(spec.params),
            "weight": spec.weight,
        }
        if spec.source:
            row["source"] = spec.source
        if spec.optional:
            row["optional"] = True
        rows.append(row)
    return rows


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
    new_connection: bool = False,
    http2: bool = False,
    batch: int = 0,
    inflight: int = 0,
    throughput: int = 0,
    logs_range: int = 0,
    profile_notes: str | None = None,
    simulate: bool = False,
    archive: bool = False,
    lookback: int = 0,
    websocket: float = 0.0,
    open_ws=None,
    family: str = FAMILY_EVM,
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
    if batch < 0 or batch > MAX_BATCH:
        raise ValueError(f"batch must be 0–{MAX_BATCH}")
    if inflight < 0 or inflight > MAX_INFLIGHT:
        raise ValueError(f"concurrency must be 0–{MAX_INFLIGHT}")
    if throughput < 0 or throughput > MAX_THROUGHPUT:
        raise ValueError(f"throughput must be 0–{MAX_THROUGHPUT}")
    if logs_range < 0 or logs_range > MAX_LOGS_RANGE:
        raise ValueError(f"logs-range must be 0–{MAX_LOGS_RANGE}")
    if lookback < 0:
        raise ValueError("lookback must be >= 0")
    if websocket < 0 or websocket > MAX_WEBSOCKET:
        raise ValueError(f"websocket must be 0–{MAX_WEBSOCKET:g}")
    if rps < 0:
        raise ValueError("rps must be >= 0")
    if mode not in {MODE_PAIRED, MODE_SEQUENTIAL}:
        raise ValueError("mode must be paired or sequential")
    rpc_params = list(params or [])
    steps = workload or (CallSpec("head", method, tuple(rpc_params)),)
    purse = RequestBudget(budget)
    deadline = None if max_duration <= 0 else time.monotonic() + max_duration
    connection = CONN_NEW if new_connection else CONN_KEEPALIVE
    http = HTTP_2 if http2 else HTTP_1
    owns_client = client is None
    if owns_client:
        client = make_client(
            timeout=timeout, new_connection=new_connection, http2=http2
        )
    transport_token = set_transport(benchmark_family(family).transport)
    try:
        payload = None
        if has_dynamic_source(steps):
            adapter = benchmark_family(family)
            steps, payload = _bind_from_chain(
                config,
                steps,
                seed=seed,
                timeout=timeout,
                purse=purse,
                deadline=deadline,
                concurrency=1 if mode == MODE_SEQUENTIAL else concurrency,
                client=client,
                head_method=adapter.head_method,
                chain_method=adapter.chain_method,
            )
            if len(steps) == 1:
                rpc_params = list(steps[0].params)
        seq_id = make_sequence_id(
            seed=seed,
            method=method,
            params=rpc_params,
            warmup=warmup,
            samples=samples,
            workload=_workload_blob(steps)
            if len(steps) > 1 or has_dynamic_source(steps)
            else None,
        )
        return _execute_run(
            config,
            method=method,
            rpc_params=rpc_params,
            samples=samples,
            warmup=warmup,
            timeout=timeout,
            budget=budget,
            purse=purse,
            deadline=deadline,
            mode=mode,
            seed=seed,
            concurrency=concurrency,
            client=client,
            steps=steps,
            profile=profile,
            sample_budget=sample_budget,
            stale_blocks=stale_blocks,
            block_time_s=block_time_s,
            block_pin=block_pin,
            burst=burst,
            rps=rps,
            seq_id=seq_id,
            connection=connection,
            http=http,
            batch=batch,
            inflight=inflight,
            throughput=throughput,
            logs_range=logs_range,
            profile_notes=profile_notes,
            payload=payload,
            simulate=simulate,
            archive=archive,
            lookback=lookback,
            websocket=websocket,
            open_ws=open_ws,
            family=family,
        )
    finally:
        reset_transport(transport_token)
        if owns_client:
            client.close()


def _bind_from_chain(
    config: BenchConfig,
    steps: tuple[CallSpec, ...],
    *,
    seed: int,
    timeout: float,
    purse: RequestBudget,
    deadline: float | None,
    concurrency: int,
    client,
    head_method: str,
    chain_method: str,
) -> tuple[tuple[CallSpec, ...], PayloadMeta]:
    """Paired extra reads to fill YAML sources. Not mixed into ranking."""
    need_head, need_chain = hint_needs(steps)
    head = None
    chain_id = None
    if need_head:
        hits = _probe_wave(
            config,
            method=head_method,
            params=[],
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            concurrency=concurrency,
            client=client,
        )
        heights = [
            parsed
            for hit in hits.values()
            if hit.ok
            for parsed in (parse_block_height(hit.result),)
            if parsed is not None
        ]
        head = cohort_height(heights)
    if need_chain:
        hits = _probe_wave(
            config,
            method=chain_method,
            params=[],
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            concurrency=concurrency,
            client=client,
        )
        for hit in hits.values():
            if not hit.ok:
                continue
            parsed = parse_block_height(hit.result)
            if parsed is not None:
                chain_id = parsed
                break
    bound, used_fixture = bind_workload(
        steps, seed=seed, head=head, chain_id=chain_id
    )
    kind = payload_kind(
        steps, head=head, chain_id=chain_id, fallback=used_fixture
    )
    return bound, PayloadMeta(
        source=kind,
        head=head,
        chain_id=chain_id,
        fallback=used_fixture or kind == "fixture",
    )


def _execute_run(
    config: BenchConfig,
    *,
    method: str,
    rpc_params: list[object],
    samples: int,
    warmup: int,
    timeout: float,
    budget: int,
    purse: RequestBudget,
    deadline: float | None,
    mode: str,
    seed: int,
    concurrency: int,
    client,
    steps: tuple[CallSpec, ...],
    profile: str,
    sample_budget: str,
    stale_blocks: int,
    block_time_s: float | None,
    block_pin: int | None,
    burst: int,
    rps: float,
    seq_id: str,
    connection: str,
    http: str,
    batch: int,
    inflight: int,
    throughput: int,
    logs_range: int,
    profile_notes: str | None,
    payload: PayloadMeta | None,
    simulate: bool,
    archive: bool,
    lookback: int,
    websocket: float,
    open_ws,
    family: str,
) -> RunResult:
    adapter = benchmark_family(family)
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
    if not any(spec.method == adapter.head_method for spec in steps):
        extra_heads = _probe_wave(
            config,
            method=adapter.head_method,
            params=[],
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            concurrency=wave_concurrency,
            client=client,
        )
    chain_id = _sample_chain_id(outcomes, adapter.chain_method)
    resolved_time = resolve_block_time(
        adapter, chain_id=chain_id, override=block_time_s
    )
    heights = {
        outcome.endpoint.name: _head_height(
            outcome, method, extra_heads, adapter.head_method
        )
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
            method=adapter.block_method,
            params=pin_block_params(adapter, pin),
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
        method=adapter.client_method,
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
    for tag in adapter.block_tags:
        hits = _probe_wave(
            config,
            method=adapter.block_method,
            params=tag_block_params(adapter, tag),
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
    if archive:
        measured_archive = _measure_archive(
            config,
            pin=pin,
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            concurrency=wave_concurrency,
            client=client,
            family=family,
        )
        outcomes = [
            replace(
                outcome,
                archive=measured_archive.get(outcome.endpoint.name),
            )
            for outcome in outcomes
        ]
    if lookback > 0:
        measured_history = _measure_history(
            config,
            pin=pin,
            lookback=lookback,
            archives={
                outcome.endpoint.name: outcome.archive for outcome in outcomes
            },
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            concurrency=wave_concurrency,
            client=client,
            family=family,
        )
        outcomes = [
            replace(
                outcome,
                history=measured_history.get(outcome.endpoint.name),
            )
            for outcome in outcomes
        ]
    if logs_range > 0:
        measured_logs = _measure_logs_ranges(
            config,
            pin=pin,
            max_blocks=logs_range,
            timeout=timeout,
            budget=purse,
            deadline=deadline,
            concurrency=wave_concurrency,
            client=client,
            family=family,
        )
        outcomes = [
            replace(
                outcome,
                logs_range=measured_logs.get(outcome.endpoint.name, ()),
            )
            for outcome in outcomes
        ]
    if batch > 0:
        spec = steps[0]
        measured = {
            outcome.endpoint.name: _measure_batch(
                outcome.endpoint,
                spec=spec,
                size=batch,
                timeout=timeout,
                budget=purse,
                deadline=deadline,
                client=client,
            )
            for outcome in outcomes
        }
        outcomes = [
            replace(outcome, batch=measured.get(outcome.endpoint.name))
            for outcome in outcomes
        ]
    if inflight > 0:
        spec = steps[0]
        measured_inflight = {
            outcome.endpoint.name: _measure_inflight(
                outcome.endpoint,
                spec=spec,
                size=inflight,
                timeout=timeout,
                budget=purse,
                deadline=deadline,
                client=client,
            )
            for outcome in outcomes
        }
        outcomes = [
            replace(outcome, inflight=measured_inflight.get(outcome.endpoint.name))
            for outcome in outcomes
        ]
    if throughput > 0:
        spec = steps[0]
        measured_throughput = {
            outcome.endpoint.name: _measure_throughput(
                outcome.endpoint,
                spec=spec,
                size=throughput,
                timeout=timeout,
                budget=purse,
                deadline=deadline,
                rps=rps,
                client=client,
            )
            for outcome in outcomes
        }
        outcomes = [
            replace(outcome, throughput=measured_throughput.get(outcome.endpoint.name))
            for outcome in outcomes
        ]
    if websocket > 0:
        measured_ws = {
            outcome.endpoint.name: probe_websocket(
                outcome.endpoint,
                window=websocket,
                timeout=timeout,
                deadline=deadline,
                family=family,
                open_ws=open_ws,
            )
            for outcome in outcomes
        }
        outcomes = [
            replace(outcome, websocket=measured_ws.get(outcome.endpoint.name))
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
        connection=connection,
        http=http,
        batch=batch,
        inflight=inflight,
        throughput=throughput,
        logs_range=logs_range,
        simulate=simulate,
        archive=archive,
        lookback=lookback,
        websocket=websocket,
        family=family,
        git_sha=current_git_sha(),
        started_at=utc_stamp(),
        vantage=vantage_label(),
        profile_notes=profile_notes,
        payload=payload,
    )


def _sample_height(
    outcome: EndpointOutcome, run_method: str, head_method: str
) -> int | None:
    for hit in outcome.samples:
        if not hit.ok:
            continue
        method = hit.method or run_method
        if method != head_method:
            continue
        height = parse_block_height(hit.result)
        if height is not None:
            return height
    return None


def _head_height(
    outcome: EndpointOutcome,
    run_method: str,
    extra: dict[str, ProbeResult],
    head_method: str,
) -> int | None:
    height = _sample_height(outcome, run_method, head_method)
    if height is not None:
        return height
    hit = extra.get(outcome.endpoint.name)
    if hit is None or not hit.ok:
        return None
    return parse_block_height(hit.result)


def _sample_chain_id(
    outcomes: list[EndpointOutcome], chain_method: str
) -> int | None:
    for outcome in outcomes:
        for hit in outcome.samples:
            if not hit.ok:
                continue
            if (hit.method or "") != chain_method:
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


def _measure_logs_ranges(
    config: BenchConfig,
    *,
    pin: int | None,
    max_blocks: int,
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    concurrency: int,
    client,
    family: str,
) -> dict[str, tuple[LogsRangeHit, ...]]:
    endpoints = list(config.endpoints)
    by_name: dict[str, list[LogsRangeHit]] = {ep.name: [] for ep in endpoints}
    reason = family_skip_reason(family)
    starved: str | None = None
    for blocks in ranges_for(max_blocks):
        if reason:
            hit = skipped_range(blocks, reason)
            for name in by_name:
                by_name[name].append(hit)
            continue
        if starved:
            hit = skipped_range(blocks, starved)
            for name in by_name:
                by_name[name].append(hit)
            continue
        span = logs_window(pin, blocks)
        if pin is None:
            hit = skipped_range(blocks, "pin")
            for name in by_name:
                by_name[name].append(hit)
            continue
        if span is None:
            hit = skipped_range(blocks, "head")
            for name in by_name:
                by_name[name].append(hit)
            continue
        start, end = span
        wave = _probe_wave(
            config,
            method="eth_getLogs",
            params=[logs_filter(start, end)],
            timeout=timeout,
            budget=budget,
            deadline=deadline,
            concurrency=concurrency,
            client=client,
        )
        for ep in endpoints:
            hit = hit_from_probe(blocks, start, end, wave[ep.name])
            by_name[ep.name].append(hit)
            if hit.skip in {"budget", "duration"}:
                starved = hit.skip
    return {name: tuple(rows) for name, rows in by_name.items()}


def _measure_archive(
    config: BenchConfig,
    *,
    pin: int | None,
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    concurrency: int,
    client,
    family: str,
) -> dict[str, ArchiveHit]:
    endpoints = list(config.endpoints)
    reason = family_skip_reason(family)
    if reason:
        hit = skipped_archive(reason)
        return {ep.name: hit for ep in endpoints}
    target = archive_block(pin)
    if pin is None:
        hit = skipped_archive("pin")
        return {ep.name: hit for ep in endpoints}
    if target is None:
        hit = skipped_archive("head")
        return {ep.name: hit for ep in endpoints}
    wave = _probe_wave(
        config,
        method="eth_getBalance",
        params=list(archive_params(target)),
        timeout=timeout,
        budget=budget,
        deadline=deadline,
        concurrency=concurrency,
        client=client,
    )
    return {
        ep.name: archive_from_probe(wave[ep.name], block=target) for ep in endpoints
    }


def _measure_history(
    config: BenchConfig,
    *,
    pin: int | None,
    lookback: int,
    archives: dict[str, ArchiveHit | None],
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    concurrency: int,
    client,
    family: str,
) -> dict[str, HistoryHit]:
    endpoints = list(config.endpoints)
    target = history_block(pin, lookback)
    by_name: dict[str, HistoryHit] = {}
    live: list[Endpoint] = []
    reuse_hist: dict[str, ProbeResult] = {}
    for ep in endpoints:
        archive = archives.get(ep.name)
        reason = history_skip_reason(
            pin=pin, lookback=lookback, family=family, archive=archive
        )
        if reason:
            by_name[ep.name] = skipped_history(reason, lookback=lookback, block=target)
            continue
        assert target is not None
        if (
            archive is not None
            and archive.ok
            and archive.block == target
            and archive.latency_ms is not None
        ):
            reuse_hist[ep.name] = ProbeResult(
                ok=True,
                reachable=True,
                latency_ms=archive.latency_ms,
                result=archive.result,
                error=None,
                error_class=None,
                attempts=1,
                method="eth_getBalance",
            )
        live.append(ep)
    if not live:
        return by_name
    live_cfg = replace(config, endpoints=tuple(live))
    head_wave = _probe_wave(
        live_cfg,
        method="eth_getBalance",
        params=list(latest_params()),
        timeout=timeout,
        budget=budget,
        deadline=deadline,
        concurrency=concurrency,
        client=client,
    )
    need_hist = [ep for ep in live if ep.name not in reuse_hist]
    hist_wave: dict[str, ProbeResult] = dict(reuse_hist)
    if need_hist:
        hist_cfg = replace(config, endpoints=tuple(need_hist))
        hist_wave.update(
            _probe_wave(
                hist_cfg,
                method="eth_getBalance",
                params=list(history_params(target if target is not None else 0)),
                timeout=timeout,
                budget=budget,
                deadline=deadline,
                concurrency=concurrency,
                client=client,
            )
        )
    for ep in live:
        by_name[ep.name] = history_from_probes(
            lookback=lookback,
            block=target if target is not None else 0,
            historical=hist_wave[ep.name],
            head=head_wave.get(ep.name),
        )
    return by_name


def _measure_batch(
    endpoint: Endpoint,
    *,
    spec: CallSpec,
    size: int,
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    client,
) -> BatchSummary:
    if _expired(deadline):
        return BatchSummary(
            size=size,
            method=spec.method,
            supported=False,
            partial=False,
            batch_ms=None,
            serial_ms=None,
            ratio=None,
            n_ok=0,
            n_fail=0,
            error="max duration exceeded",
            error_class="duration",
        )
    batched = probe_batch(
        endpoint.url,
        spec.method,
        params=list(spec.params),
        size=size,
        timeout=timeout,
        budget=budget,
        client=client,
        headers=endpoint.headers,
    )
    serial_started = time.monotonic()
    for _ in range(size):
        if _expired(deadline):
            break
        probe(
            endpoint.url,
            spec.method,
            params=list(spec.params),
            timeout=timeout,
            retries=0,
            budget=budget,
            client=client,
            headers=endpoint.headers,
        )
    serial_ms = (time.monotonic() - serial_started) * 1000
    ratio = None
    if batched.latency_ms and batched.latency_ms > 0:
        ratio = serial_ms / batched.latency_ms
    return BatchSummary(
        size=size,
        method=spec.method,
        supported=batched.supported,
        partial=batched.partial,
        batch_ms=batched.latency_ms,
        serial_ms=serial_ms,
        ratio=ratio,
        n_ok=batched.n_ok,
        n_fail=batched.n_fail,
        error=batched.error,
        error_class=batched.error_class,
    )


def _skipped_inflight(
    size: int, method: str, error_class: str, error: str
) -> ConcurrentSummary:
    return ConcurrentSummary(
        size=size,
        method=method,
        concurrent_p50_ms=None,
        concurrent_p95_ms=None,
        serial_p50_ms=None,
        serial_p95_ms=None,
        concurrent_ms=None,
        serial_ms=None,
        ratio=None,
        n_ok=0,
        n_fail=0,
        error=error,
        error_class=error_class,
    )


def _measure_inflight(
    endpoint: Endpoint,
    *,
    spec: CallSpec,
    size: int,
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    client,
) -> ConcurrentSummary:
    if _expired(deadline):
        return _skipped_inflight(
            size, spec.method, "duration", "max duration exceeded"
        )

    def one() -> ProbeResult:
        return _hit(
            endpoint,
            spec=spec,
            timeout=timeout,
            budget=budget,
            deadline=deadline,
            client=client,
        )

    conc_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, size)) as pool:
        futs = [_submit(pool, one) for _ in range(size)]
        concurrent = tuple(fut.result() for fut in futs)
    concurrent_ms = (time.monotonic() - conc_started) * 1000
    serial_started = time.monotonic()
    serial = tuple(one() for _ in range(size))
    serial_ms = (time.monotonic() - serial_started) * 1000
    conc_stats = summarize(concurrent)
    ser_stats = summarize(serial)
    ratio = None
    if (
        conc_stats.p50_ms is not None
        and ser_stats.p50_ms is not None
        and ser_stats.p50_ms > 0
    ):
        ratio = conc_stats.p50_ms / ser_stats.p50_ms
    error = None
    error_class = None
    if conc_stats.n_ok == 0:
        miss = next((hit for hit in concurrent if hit.error_class), None)
        if miss is not None:
            error = miss.error
            error_class = miss.error_class
    return ConcurrentSummary(
        size=size,
        method=spec.method,
        concurrent_p50_ms=conc_stats.p50_ms,
        concurrent_p95_ms=conc_stats.p95_ms,
        serial_p50_ms=ser_stats.p50_ms,
        serial_p95_ms=ser_stats.p95_ms,
        concurrent_ms=concurrent_ms,
        serial_ms=serial_ms,
        ratio=ratio,
        n_ok=conc_stats.n_ok,
        n_fail=conc_stats.n_fail,
        error=error,
        error_class=error_class,
    )


def _skipped_throughput(
    size: int, method: str, error_class: str, error: str
) -> ThroughputSummary:
    return ThroughputSummary(
        size=size,
        method=method,
        rps=None,
        duration_ms=None,
        n_ok=0,
        n_fail=0,
        n=0,
        rate_limit=0,
        error=error,
        error_class=error_class,
    )


def _measure_throughput(
    endpoint: Endpoint,
    *,
    spec: CallSpec,
    size: int,
    timeout: float,
    budget: RequestBudget,
    deadline: float | None,
    rps: float,
    client,
) -> ThroughputSummary:
    if _expired(deadline):
        return _skipped_throughput(
            size, spec.method, "duration", "max duration exceeded"
        )
    hits: list[ProbeResult] = []
    last_start: float | None = None
    started = time.monotonic()
    for _ in range(size):
        if _expired(deadline):
            break
        last_start = _pace(last_start, rps)
        hits.append(
            _hit(
                endpoint,
                spec=spec,
                timeout=timeout,
                budget=budget,
                deadline=deadline,
                client=client,
            )
        )
    duration_ms = (time.monotonic() - started) * 1000
    if not hits:
        return _skipped_throughput(
            size, spec.method, "duration", "max duration exceeded"
        )
    stats = summarize(tuple(hits))
    elapsed_s = duration_ms / 1000.0
    ok_rps = (stats.n_ok / elapsed_s) if elapsed_s > 0 else None
    error = None
    error_class = None
    if stats.n_ok == 0:
        miss = next((hit for hit in hits if hit.error_class), None)
        if miss is not None:
            error = miss.error
            error_class = miss.error_class
    return ThroughputSummary(
        size=size,
        method=spec.method,
        rps=ok_rps,
        duration_ms=duration_ms,
        n_ok=stats.n_ok,
        n_fail=stats.n_fail,
        n=stats.n_ok + stats.n_fail,
        rate_limit=dict(stats.by_class).get("rate_limit", 0),
        error=error,
        error_class=error_class,
    )


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
        futs = {ep.name: _submit(pool, fire, ep) for ep in endpoints}
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


def _ranking_samples(
    measured: tuple[ProbeResult, ...], workload: tuple[CallSpec, ...]
) -> tuple[ProbeResult, ...]:
    """Drop optional skip hits so unimplemented/restricted traces are not a fail."""
    optional = {spec.method for spec in workload if spec.optional}
    if not optional:
        return measured
    kept = []
    for hit in measured:
        method = hit.method or ""
        if method in optional and optional_skip_reason(hit) is not None:
            continue
        kept.append(hit)
    return tuple(kept)


def _finish_outcome(
    endpoint: Endpoint,
    warmup_hits: tuple[ProbeResult, ...],
    measured: tuple[ProbeResult, ...],
    workload: tuple[CallSpec, ...],
    burst: int = 0,
) -> EndpointOutcome:
    ranked = _ranking_samples(measured, workload)
    burst_stats, steady_stats = _phase_stats(ranked, burst)
    return EndpointOutcome(
        endpoint=endpoint,
        warmup=warmup_hits,
        samples=measured,
        stats=summarize(ranked),
        by_method=_by_method(measured, workload),
        burst_stats=burst_stats,
        steady_stats=steady_stats,
        timing=summarize_timing(ranked),
        transport=summarize_transport(ranked),
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
                futs = {ep.name: _submit(pool, fire, ep, spec) for ep in endpoints}
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
                    (index, ep.name): _submit(pool, fire, ep, spec)
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
                _submit(pool, 
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
