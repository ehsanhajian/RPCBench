from __future__ import annotations

from rpcbench.config import Endpoint
from rpcbench.coverage import missed_steps
from rpcbench.methods import MIX_PROFILE
from rpcbench.reliability import (
    COVERAGE_WEIGHT,
    ERROR_WEIGHT,
    TAIL_WEIGHT,
    TIMEOUT_WEIGHT,
    assess,
)
from rpcbench.report import format_run
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, summarize


def _ok(ms: float, method: str = "eth_blockNumber") -> ProbeResult:
    return ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=ms,
        result="0x1",
        error=None,
        error_class=None,
        attempts=1,
        method=method,
    )


def _fail(
    error_class: str = "timeout",
    error: str = "took too long",
    method: str = "eth_blockNumber",
) -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=False,
        latency_ms=5.0,
        result=None,
        error=error,
        error_class=error_class,
        attempts=1,
        method=method,
    )


def _outcome(samples: tuple[ProbeResult, ...], **kwargs: object) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name="a", url="http://127.0.0.1/a"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        **kwargs,  # type: ignore[arg-type]
    )


def test_all_errors_score_zero() -> None:
    rel = assess(_outcome((_fail(), _fail(), _fail())))
    assert rel.score == 0
    assert rel.success_rate == 0.0
    assert rel.timeout_share == 1.0
    assert rel.errors == 0.0


def test_clean_equal_samples_score_100() -> None:
    rel = assess(_outcome((_ok(10.0), _ok(10.0), _ok(10.0))))
    assert rel.score == 100
    assert rel.success_rate == 1.0
    assert rel.timeout_share == 0.0
    assert rel.p99_p50 == 1.0
    assert rel.coverage == 1.0
    assert rel.errors == ERROR_WEIGHT
    assert rel.timeouts == TIMEOUT_WEIGHT
    assert rel.tail == TAIL_WEIGHT
    assert rel.coverage_points == COVERAGE_WEIGHT


def test_timeout_hurts_more_than_jsonrpc() -> None:
    timeout = assess(_outcome((_ok(10.0), _fail("timeout", "slow"))))
    jsonrpc = assess(_outcome((_ok(10.0), _fail("jsonrpc", "revert"))))
    assert timeout.score < jsonrpc.score
    assert timeout.timeout_share == 0.5
    assert jsonrpc.timeout_share == 0.0


def test_mix_coverage_gap_lowers_score() -> None:
    hits: list[ProbeResult] = []
    rows = []
    for spec in MIX_PROFILE:
        if spec.name == "logs":
            chunk = (_fail("jsonrpc", "filter", spec.method),)
        else:
            chunk = (_ok(10.0, spec.method),)
        hits.extend(chunk)
        rows.append((spec.name, summarize(chunk)))
    outcome = _outcome(tuple(hits), by_method=tuple(rows))
    rel = assess(outcome)
    full = assess(_outcome((_ok(10.0), _ok(10.0))))
    assert rel.coverage == 5 / 6
    assert rel.coverage_points == COVERAGE_WEIGHT * 5 / 6
    assert rel.score < full.score
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=1,
        warmup=0,
        timeout=5.0,
        budget=16,
        outcomes=(outcome,),
        budget_remaining=10,
        profile="mix",
        workload=MIX_PROFILE,
    )
    assert missed_steps(outcome, result) == ("logs",)
    text = format_run(result, verbose=True, color=False)
    assert "Reliability  (0–100 this run" in text
    assert "finding" not in text.lower()
    assert "severity" not in text.lower()


def test_score_is_deterministic() -> None:
    samples = (_ok(10.0), _ok(12.0), _fail("timeout", "slow"))
    left = assess(_outcome(samples))
    right = assess(_outcome(samples))
    assert left == right
    assert left.score == right.score


def test_compact_ranking_shows_rel() -> None:
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=3,
        warmup=0,
        timeout=10.0,
        budget=8,
        outcomes=(_outcome((_ok(10.0), _ok(10.0), _ok(10.0))),),
        budget_remaining=5,
    )
    text = format_run(result, color=False)
    assert "rel" in text
    assert "100" in text
    assert "Reliability  (0–100 this run" not in text
