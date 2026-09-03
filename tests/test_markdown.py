from __future__ import annotations

from rpcbench.config import Endpoint
from rpcbench.freshness import Freshness
from rpcbench.markdown import format_md
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, summarize
from rpcbench.watermark import DOCS_BOUNDARY, DOCS_METHODOLOGY


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


def _fail(error_class: str = "timeout", error: str = "took too long") -> ProbeResult:
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
    url: str | None = None,
) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name=name, url=url or f"http://127.0.0.1/{name}"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        freshness=freshness,
    )


def _compare() -> RunResult:
    return RunResult(
        method="eth_blockNumber",
        params=(),
        samples=2,
        warmup=0,
        timeout=10.0,
        budget=16,
        outcomes=(
            _outcome(
                "lagged",
                (_ok(8.0), _ok(8.0)),
                freshness=_fresh(97, 3, "stale"),
            ),
            _outcome(
                "tip",
                (_ok(20.0), _ok(20.0)),
                freshness=_fresh(100, 0, "fresh"),
            ),
            _outcome("dead", (_fail(),)),
        ),
        budget_remaining=10,
        git_sha="deadbeef0001",
        started_at="2026-08-25T12:00:00Z",
        vantage="lab",
    )


def test_md_is_pasteable_github_table() -> None:
    text = format_md(_compare())
    assert text.startswith("# RPCBench\n")
    assert "| # | name | p95 | err | fresh | verdict |" in text
    assert "| --- | --- | --- | --- | --- | --- |" in text
    assert "20.0ms" in text
    assert "stale" in text
    assert "**Primary** tip" in text
    assert "**Fastest**" in text
    assert "## Signals" in text
    assert "problem" in text
    assert "next" in text
    assert DOCS_METHODOLOGY in text
    assert DOCS_BOUNDARY in text
    assert "deadbeef0001" in text
    assert "finding" not in text.lower()
    assert "severity" not in text.lower()
    assert "↳" not in text
    assert "Critical" not in text


def test_md_redacts_url_secrets() -> None:
    secret = "query_secret"
    outcome = _outcome(
        "paid",
        (_ok(10.0),),
        url=(
            "https://rpc.example/v3/abcdabcdabcdabcdabcdabcdabcdabcd"
            f"?apiKey={secret}"
        ),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=1,
        warmup=0,
        timeout=5.0,
        budget=8,
        outcomes=(outcome,),
        budget_remaining=4,
    )
    text = format_md(result)
    assert secret not in text
    assert "apiKey" not in text


def test_md_stale_fast_is_not_primary() -> None:
    text = format_md(_compare())
    primary = text.split("**Primary**", 1)[1].split("**Fallback**", 1)[0]
    assert "tip" in primary
    assert "lagged" not in primary
