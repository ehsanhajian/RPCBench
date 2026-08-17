"""Timed JSON-RPC run: warmup then samples, min/mean/max on successes."""

from __future__ import annotations

from dataclasses import dataclass

from rpcbench.config import BenchConfig, Endpoint
from rpcbench.rpc import ProbeResult, RequestBudget, probe
from rpcbench.urls import display_url


@dataclass(frozen=True)
class LatencyStats:
    n_ok: int
    n_fail: int
    min_ms: float | None
    mean_ms: float | None
    max_ms: float | None


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


def summarize(samples: tuple[ProbeResult, ...]) -> LatencyStats:
    ok = [s.latency_ms for s in samples if s.ok and s.latency_ms is not None]
    n_fail = sum(1 for s in samples if not s.ok)
    if not ok:
        return LatencyStats(
            n_ok=0, n_fail=n_fail, min_ms=None, mean_ms=None, max_ms=None
        )
    return LatencyStats(
        n_ok=len(ok),
        n_fail=n_fail,
        min_ms=min(ok),
        mean_ms=sum(ok) / len(ok),
        max_ms=max(ok),
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
    client=None,
) -> RunResult:
    if samples < 1:
        raise ValueError("samples must be at least 1")
    if warmup < 0:
        raise ValueError("warmup must be >= 0")
    purse = RequestBudget(budget)
    rpc_params = list(params or [])
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


def _run_one(
    endpoint: Endpoint,
    *,
    method: str,
    params: list[object],
    samples: int,
    warmup: int,
    timeout: float,
    budget: RequestBudget,
    client,
) -> EndpointOutcome:
    warmup_hits: list[ProbeResult] = []
    measured: list[ProbeResult] = []
    stop = False
    for _ in range(warmup):
        hit = probe(
            endpoint.url,
            method,
            params=params,
            timeout=timeout,
            retries=0,
            budget=budget,
            client=client,
        )
        warmup_hits.append(hit)
        if hit.error_class in {"invalid_url", "budget"}:
            stop = True
            break
    if not stop:
        for _ in range(samples):
            hit = probe(
                endpoint.url,
                method,
                params=params,
                timeout=timeout,
                retries=0,
                budget=budget,
                client=client,
            )
            measured.append(hit)
            if hit.error_class in {"invalid_url", "budget"}:
                break
    return EndpointOutcome(
        endpoint=endpoint,
        warmup=tuple(warmup_hits),
        samples=tuple(measured),
        stats=summarize(tuple(measured)),
    )


def format_run(result: RunResult) -> str:
    ok_n = sum(1 for o in result.outcomes if o.stats.n_ok)
    fail_n = len(result.outcomes) - ok_n
    params = f" {list(result.params)}" if result.params else ""
    lines = [
        "RPCBench run",
        "=" * 72,
        f"Method:   {result.method}{params}",
        f"Samples:  {result.samples}  Warmup: {result.warmup}  "
        f"Timeout: {result.timeout:g}s  "
        f"Budget: {result.budget} ({result.budget_remaining} left)",
        "",
    ]
    name_w = max((len(o.endpoint.name) for o in result.outcomes), default=4)
    for outcome in result.outcomes:
        stats = outcome.stats
        status = "ok" if stats.n_ok else "fail"
        n = f"n={stats.n_ok}/{stats.n_ok + stats.n_fail}"
        if stats.min_ms is not None:
            detail = (
                f"{n}  min={stats.min_ms:.1f}ms  "
                f"mean={stats.mean_ms:.1f}ms  max={stats.max_ms:.1f}ms"
            )
        else:
            err = outcome.samples[-1] if outcome.samples else (
                outcome.warmup[-1] if outcome.warmup else None
            )
            if err and err.error:
                detail = f"{n}  {err.error_class}: {err.error}"
            else:
                detail = n
        url = display_url(outcome.endpoint.url)
        lines.append(
            f"  {outcome.endpoint.name:<{name_w}}  {status:<4}  {detail}"
        )
        lines.append(f"  {'':<{name_w}}         {url}")
    lines.append("")
    lines.append(
        f"{ok_n} ok  {fail_n} failed  ·  warmup excluded  ·  min/mean/max of successful samples"
    )
    return "\n".join(lines) + "\n"
