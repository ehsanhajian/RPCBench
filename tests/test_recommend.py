from __future__ import annotations

from rpcbench.config import Endpoint
from rpcbench.consistency import Consistency
from rpcbench.freshness import Freshness
from rpcbench.recommend import html_block, recommend
from rpcbench.report import format_run, place_outcomes, run_to_dict
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, summarize
from rpcbench.verdict import READY, assess as assess_verdict


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


def _agree(digest: str, verdict: str = "agree") -> Consistency:
    return Consistency(
        hash=digest,
        number=100,
        verdict=verdict,
        pin_height=100,
        canonical_hash=digest if verdict == "agree" else "0x" + "aa" * 32,
    )


def _outcome(
    name: str,
    samples: tuple[ProbeResult, ...],
    *,
    freshness: Freshness | None = None,
    consistency: Consistency | None = None,
) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name=name, url=f"http://127.0.0.1/{name}"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        freshness=freshness,
        consistency=consistency,
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


def _rows(result: RunResult) -> list:
    placed = place_outcomes(result)
    out = []
    for row in placed:
        out.append(
            (
                row.outcome,
                row.rank,
                assess_verdict(
                    row.outcome,
                    rank=row.rank,
                    similar=row.similar,
                    result=result,
                ),
            )
        )
    return out


def test_two_ready_names_primary_and_fallback() -> None:
    result = _result(
        _outcome("slow", (_ok(40.0), _ok(50.0)), freshness=_fresh(100, 0, "fresh")),
        _outcome("fast", (_ok(10.0), _ok(12.0)), freshness=_fresh(100, 0, "fresh")),
    )
    route = recommend(_rows(result))
    assert route.primary == "fast"
    assert route.fallback == "slow"
    assert "fast is ready" in route.why
    assert "slow is the next ready endpoint" in route.why
    text = format_run(result, color=False)
    assert "Route  (this workload, this run; not an SLA)" in text
    assert "Primary   fast" in text
    assert "Fallback  slow" in text
    assert "finding" not in text.lower()
    data = run_to_dict(result)
    assert data["route"]["primary"] == "fast"
    assert data["route"]["fallback"] == "slow"
    assert data["summary"]["primary"] == "fast"
    assert data["summary"]["fallback"] == "slow"


def test_stale_fast_is_not_primary() -> None:
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
    other = _outcome(
        "other",
        (_ok(22.0), _ok(22.0)),
        freshness=_fresh(100, 0, "fresh"),
    )
    result = _result(lagged, tip, other)
    route = recommend(_rows(result))
    assert route.primary == "tip"
    assert route.fallback == "other"
    assert route.primary != "lagged"
    text = format_run(result, color=False)
    assert "Primary   tip" in text
    assert "lagged" not in text.split("Primary", 1)[1].split("Fallback", 1)[0]
    data = run_to_dict(result)
    assert data["route"]["primary"] == "tip"
    assert data["summary"]["not_ready_names"] == ["lagged"]


def test_disagree_fast_is_not_primary() -> None:
    digest_a = "0x" + "aa" * 32
    digest_b = "0x" + "bb" * 32
    wrong = _outcome(
        "wrong",
        (_ok(8.0), _ok(8.0)),
        consistency=_agree(digest_b, verdict="disagree"),
    )
    right = _outcome(
        "right",
        (_ok(20.0), _ok(20.0)),
        consistency=_agree(digest_a),
    )
    result = _result(wrong, right)
    route = recommend(_rows(result))
    assert route.primary == "right"
    assert route.fallback is None
    assert "no second ready endpoint" in route.why


def test_one_ready_has_primary_without_fallback() -> None:
    result = _result(
        _outcome("only", (_ok(10.0), _ok(10.0))),
        _outcome("dead", (_fail("timeout"),)),
    )
    route = recommend(_rows(result))
    assert route.primary == "only"
    assert route.fallback is None
    text = format_run(result, color=False)
    assert "Primary   only" in text
    assert "Fallback  none" in text


def test_no_ready_has_neither() -> None:
    result = _result(
        _outcome("dead", (_fail("timeout"),)),
        _outcome("down", (_fail("connection"),)),
    )
    route = recommend(_rows(result))
    assert route.primary is None
    assert route.fallback is None
    text = format_run(result, color=False)
    assert "Primary   none" in text
    assert "Fallback  none" in text
    assert "No ready endpoint" in text


def test_similar_band_prefers_higher_rel() -> None:
    hot = _outcome(
        "hot",
        tuple(_ok(10.0) for _ in range(10)) + (_fail("jsonrpc", "revert"),),
        freshness=_fresh(100, 0, "fresh"),
    )
    solid = _outcome(
        "solid",
        tuple(_ok(11.0) for _ in range(11)),
        freshness=_fresh(100, 0, "fresh"),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=11,
        warmup=0,
        timeout=10.0,
        budget=32,
        outcomes=(hot, solid),
        budget_remaining=10,
    )
    assert assess_verdict(hot, rank=1, similar=True).decision == READY
    route = recommend(_rows(result))
    assert route.primary == "solid"
    assert route.fallback == "hot"
    assert "jsonrpc" in route.why


def test_fallback_skips_same_error_class() -> None:
    noisy = tuple(_ok(10.0) for _ in range(10)) + (_fail("jsonrpc", "revert"),)
    also = tuple(_ok(10.5) for _ in range(10)) + (_fail("jsonrpc", "revert"),)
    clean = tuple(_ok(20.0) for _ in range(11))
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=11,
        warmup=0,
        timeout=10.0,
        budget=48,
        outcomes=(
            _outcome("noisy", noisy, freshness=_fresh(100, 0, "fresh")),
            _outcome("also", also, freshness=_fresh(100, 0, "fresh")),
            _outcome("clean", clean, freshness=_fresh(100, 0, "fresh")),
        ),
        budget_remaining=10,
    )
    route = recommend(_rows(result))
    assert route.primary == "noisy"
    assert route.fallback == "clean"
    assert route.fallback != "also"
    assert "jsonrpc" in route.why


def test_html_block_is_operational_not_scanner() -> None:
    result = _result(
        _outcome("slow", (_ok(40.0), _ok(50.0))),
        _outcome("fast", (_ok(10.0), _ok(12.0))),
    )
    html = html_block(recommend(_rows(result)))
    assert 'aria-label="route"' in html
    assert "Primary fast" in html
    assert "Fallback slow" in html
    assert "finding" not in html.lower()
    assert "severity" not in html.lower()
    assert "↳" not in html
    assert "Critical" not in html
