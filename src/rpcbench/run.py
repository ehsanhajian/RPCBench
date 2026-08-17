"""Run a cheap reachability probe against every configured endpoint."""

from __future__ import annotations

from dataclasses import dataclass

from rpcbench.config import BenchConfig, Endpoint
from rpcbench.rpc import ProbeResult, RequestBudget, probe
from rpcbench.urls import display_url


@dataclass(frozen=True)
class EndpointOutcome:
    endpoint: Endpoint
    probe: ProbeResult


@dataclass(frozen=True)
class RunResult:
    method: str
    timeout: float
    retries: int
    budget: int
    outcomes: tuple[EndpointOutcome, ...]
    budget_remaining: int


def run_endpoints(
    config: BenchConfig,
    *,
    method: str = "eth_blockNumber",
    timeout: float = 10.0,
    retries: int = 2,
    budget: int = 32,
    client=None,
) -> RunResult:
    purse = RequestBudget(budget)
    outcomes: list[EndpointOutcome] = []
    for endpoint in config.endpoints:
        result = probe(
            endpoint.url,
            method,
            timeout=timeout,
            retries=retries,
            budget=purse,
            client=client,
        )
        outcomes.append(EndpointOutcome(endpoint=endpoint, probe=result))
    return RunResult(
        method=method,
        timeout=timeout,
        retries=retries,
        budget=budget,
        outcomes=tuple(outcomes),
        budget_remaining=purse.remaining,
    )


def format_run(result: RunResult) -> str:
    ok_n = sum(1 for o in result.outcomes if o.probe.ok)
    fail_n = len(result.outcomes) - ok_n
    lines = [
        "RPCBench run",
        "=" * 72,
        f"Method:   {result.method}",
        f"Timeout:  {result.timeout:g}s  Retries: {result.retries}  "
        f"Budget: {result.budget} ({result.budget_remaining} left)",
        "",
    ]
    name_w = max((len(o.endpoint.name) for o in result.outcomes), default=4)
    for outcome in result.outcomes:
        probe_result = outcome.probe
        status = "ok" if probe_result.ok else "fail"
        latency = (
            f"{probe_result.latency_ms:.1f}ms"
            if probe_result.latency_ms is not None
            else "-"
        )
        if probe_result.ok:
            detail = f"result={_short(probe_result.result)}"
        else:
            klass = probe_result.error_class or "error"
            detail = f"{klass}: {probe_result.error}"
        url = display_url(outcome.endpoint.url)
        lines.append(
            f"  {outcome.endpoint.name:<{name_w}}  {status:<4}  {latency:>8}  {detail}"
        )
        lines.append(f"  {'':<{name_w}}         {url}")
    lines.append("")
    lines.append(
        f"{ok_n} ok  {fail_n} failed  ·  this is a reachability probe, not a benchmark"
    )
    return "\n".join(lines) + "\n"


def _short(value: object, limit: int = 48) -> str:
    text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text
