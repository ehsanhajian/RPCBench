from __future__ import annotations

from rpcbench.config import Endpoint
from rpcbench.freshness import Freshness
from rpcbench.html import format_html
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
    url: str | None = None,
    freshness: Freshness | None = None,
) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name=name, url=url or f"http://127.0.0.1/{name}"),
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
        git_sha="deadbeef0001",
        started_at="2026-08-25T12:00:00Z",
        vantage="lab",
    )


def _compare() -> RunResult:
    return _result(
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
    )


def test_html_is_standalone_offline() -> None:
    html = format_html(_compare())
    assert html.startswith("<!DOCTYPE html>")
    assert 'charset="utf-8"' in html
    assert "<style>" in html
    assert "<svg" in html
    assert "<script" not in html.lower()
    assert "cdn" not in html.lower()
    assert 'src="http' not in html.lower()
    assert 'src="//' not in html
    assert "<link" not in html.lower()
    assert "finding" not in html.lower()
    assert "severity" not in html.lower()
    assert "↳" not in html
    assert "Critical" not in html


def test_html_fold_shows_ranking_p95_err_freshness() -> None:
    html = format_html(_compare())
    fold = html.split("<!-- fold -->", 1)[0]
    assert "<th>head</th>" in fold
    assert 'class="heat ok"' in fold
    assert 'class="heat miss"' in fold
    assert "timeout" in fold
    assert "problem" in fold
    assert "next" in fold
    assert "finding" not in html.lower()
    assert "p95" in fold
    assert "err" in fold
    assert "fresh" in fold
    assert "tip" in fold
    assert "20.0ms" in fold
    assert "yes" in fold
    assert "stale" in fold
    assert "Primary" in fold
    assert "rel" in fold
    assert "Heatmap" in fold
    assert "Signals" in fold
    assert "samples" in fold
    assert "<polyline" in fold


def test_html_uses_status_color_and_aligned_columns() -> None:
    html = format_html(_compare())
    fold = html.split("<!-- fold -->", 1)[0]
    assert 'class="ok"' in fold
    assert 'class="bad"' in fold
    assert 'class="stale"' in fold
    assert 'class="label">Fastest</span>' in fold
    assert 'class="label">Primary</span>' in fold
    assert 'th class="num"' in fold
    assert "width: auto" in html
    assert "finding" not in html.lower()
    assert "severity" not in html.lower()
    assert "size vs latency" not in html


def test_html_heatmap_and_signals_are_performance_not_findings() -> None:
    html = format_html(_compare())
    fold = html.split("<!-- fold -->", 1)[0]
    assert "<th>head</th>" in fold
    assert 'class="heat ok"' in fold
    assert 'class="heat miss"' in fold
    assert "timeout" in fold
    assert "problem" in fold
    assert "next" in fold
    assert "finding" not in html.lower()
    assert "severity" not in html.lower()


def test_html_reuses_watermark_footer() -> None:
    html = format_html(_compare())
    assert "deadbeef0001" in html
    assert "lab" in html
    assert "docs/METHODOLOGY.md" in html
    assert "docs/BOUNDARY.md" in html
    assert "<footer>" in html


def test_html_has_charts_and_sections() -> None:
    html = format_html(_compare())
    assert 'aria-label="P95"' in html
    assert "Histogram" in html
    assert "Comparison" in html
    assert "Capabilities" in html
    assert "Errors" in html
    assert "timeout" in html
    assert 'aria-label="heatmap"' in html
    assert 'aria-label="signals"' in html
    assert "@media print" in html
    assert "print-color-adjust" in html
    assert "Primary" in html
    assert "Fallback" in html
    assert 'dominant-baseline="central"' in html
    assert "successful samples per latency bucket" in html
    assert "heat-name" in html
    assert "finding" not in html.lower()


def test_html_redacts_url_secrets() -> None:
    secret = "query_secret"
    outcome = _outcome(
        "paid",
        (_ok(10.0),),
        url=(
            "https://rpc.example/v3/abcdabcdabcdabcdabcdabcdabcdabcd"
            f"?apiKey={secret}"
        ),
    )
    html = format_html(_result(outcome))
    assert secret not in html
    assert "apiKey" not in html


def test_html_stale_fast_is_not_primary() -> None:
    html = format_html(_compare())
    hero = html.split("<!-- fold -->", 1)[0]
    assert "Primary" in hero
    assert "tip" in hero
    assert "lagged" not in hero.split("Primary", 1)[1].split("Fallback", 1)[0]


def test_html_mix_includes_methods() -> None:
    from rpcbench.methods import MIX_PROFILE

    rows = []
    hits: list[ProbeResult] = []
    for spec in MIX_PROFILE:
        chunk = (_ok(10.0, spec.method), _ok(12.0, spec.method))
        hits.extend(chunk)
        rows.append((spec.name, summarize(chunk)))
    outcome = EndpointOutcome(
        endpoint=Endpoint(name="node", url="http://127.0.0.1/node"),
        warmup=(),
        samples=tuple(hits),
        stats=summarize(tuple(hits)),
        by_method=tuple(rows),
        freshness=_fresh(100, 0, "fresh"),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=2,
        warmup=0,
        timeout=5.0,
        budget=32,
        outcomes=(outcome,),
        budget_remaining=20,
        profile="mix",
        workload=MIX_PROFILE,
    )
    html = format_html(result)
    fold = html.split("<!-- fold -->", 1)[0]
    assert "Methods" in html
    assert "eth_getLogs" in html
    assert "eth_call" in html
    assert 'aria-label="heatmap"' in fold
    assert "head" in fold
    assert "logs" in fold
    assert "chainId" in fold
    assert "finding" not in html.lower()
