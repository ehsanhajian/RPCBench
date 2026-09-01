from __future__ import annotations

from rpcbench.config import Endpoint
from rpcbench.freshness import Freshness
from rpcbench.report import format_run, run_to_dict
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, summarize
from rpcbench.verdict import (
    KIND_FAST,
    KIND_RATE_LIMIT,
    KIND_STALE,
    KIND_TIMEOUT,
    NOT_READY,
    READY,
    RISKY,
    assess,
    html_block,
)


def _ok(ms: float) -> ProbeResult:
    return ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=ms,
        result="0x1",
        error=None,
        error_class=None,
        attempts=1,
    )


def _fail(error_class: str, error: str = "err") -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=False,
        latency_ms=5.0,
        result=None,
        error=error,
        error_class=error_class,
        attempts=1,
    )


def _fresh(height: int, lag: int, verdict: str) -> Freshness:
    return Freshness(
        height=height,
        height_hex=hex(height),
        lag_blocks=lag,
        lag_s=lag * 12.0,
        verdict=verdict,
        cohort_height=100,
    )


def _outcome(
    name: str,
    samples: tuple[ProbeResult, ...],
    *,
    freshness: Freshness | None = None,
) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name=name, url=f"http://127.0.0.1/{name}"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        freshness=freshness,
    )


def _result(*outcomes: EndpointOutcome) -> RunResult:
    return RunResult(
        method="eth_blockNumber",
        params=(),
        samples=2,
        warmup=0,
        timeout=10.0,
        budget=16,
        outcomes=outcomes,
        budget_remaining=10,
    )


def test_clean_winner_is_ready_fast_stable() -> None:
    winner = _outcome("fast", (_ok(10.0), _ok(10.0)))
    mark = assess(winner, rank=1, similar=False)
    assert mark.decision == READY
    assert mark.kind == KIND_FAST
    assert mark.signals == ()
    assert mark.cli_decision() == "ready"


def test_forced_stale_is_not_ready_performance_signal() -> None:
    lagged = _outcome(
        "lagged",
        (_ok(8.0), _ok(8.0)),
        freshness=_fresh(97, 3, "stale"),
    )
    tip = _outcome(
        "tip",
        (_ok(20.0), _ok(20.0)),
        freshness=_fresh(100, 0, "fresh"),
    )
    mark = assess(lagged, rank=None, similar=False)
    assert mark.decision == NOT_READY
    assert mark.kind == KIND_STALE
    assert mark.signals[0].id == KIND_STALE
    assert mark.signals[0].problem
    assert mark.signals[0].why
    assert mark.signals[0].next
    result = _result(lagged, tip)
    text = format_run(result, color=False)
    assert "Verdict" in text
    assert "not ready" in text
    assert "lagged (stale)" in text
    full = format_run(result, verbose=True, color=False)
    assert "Signals" in full
    assert "problem  Head lag exceeds" in full
    assert "why      Reads can be behind" in full
    assert "next     Prefer a fresher endpoint" in full
    assert "finding" not in full.lower()
    assert "severity" not in full.lower()
    assert "↳" not in full
    data = run_to_dict(result)
    row = next(item for item in data["ranking"] if item["name"] == "lagged")
    assert row["verdict"]["decision"] == NOT_READY
    assert row["verdict"]["kind"] == KIND_STALE
    assert data["summary"]["not_ready_names"] == ["lagged"]


def test_forced_429_with_oks_is_risky_rate_limited() -> None:
    node = _outcome(
        "node",
        (_ok(10.0), _ok(12.0), _fail("rate_limit", "Too Many Requests")),
    )
    mark = assess(node, rank=None, similar=False)
    assert mark.decision == RISKY
    assert mark.kind == KIND_RATE_LIMIT
    assert any(sig.id == KIND_RATE_LIMIT for sig in mark.signals)
    result = _result(node)
    text = format_run(result, color=False)
    assert "risky" in text
    assert "node (rate-limited)" in text
    full = format_run(result, verbose=True, color=False)
    assert "Timed samples returned 429" in full
    assert "finding" not in full.lower()
    data = run_to_dict(result)
    assert data["ranking"][0]["verdict"]["kind"] == KIND_RATE_LIMIT
    assert data["summary"]["risky_names"] == ["node"]


def test_all_timeouts_are_not_ready() -> None:
    dead = _outcome("dead", (_fail("timeout", "took too long"),))
    mark = assess(dead, rank=None, similar=False)
    assert mark.decision == NOT_READY
    assert mark.kind == KIND_TIMEOUT
    assert mark.cli_decision() == "not ready"


def test_html_block_is_operational_not_scanner() -> None:
    lagged = _outcome(
        "lagged",
        (_ok(8.0), _ok(8.0)),
        freshness=_fresh(97, 3, "stale"),
    )
    mark = assess(lagged, rank=None, similar=False)
    html = html_block([("lagged", mark)])
    assert "<section" in html
    assert "not ready" in html
    assert "stale" in html
    assert "<dt>problem</dt>" in html
    assert "<dt>why</dt>" in html
    assert "<dt>next</dt>" in html
    assert "finding" not in html.lower()
    assert "severity" not in html.lower()
    assert "↳" not in html
    assert "Critical" not in html
