from __future__ import annotations

from rpcbench.config import Endpoint
from rpcbench.consistency import Consistency
from rpcbench.freshness import Freshness
from rpcbench.report import color_enabled, format_run, place_outcomes, rank_outcomes
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, summarize
from rpcbench.tags import TagSnapshot


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


def _fail(error_class: str = "connection", error: str = "refused") -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=False,
        latency_ms=5.0,
        result=None,
        error=error,
        error_class=error_class,
        attempts=1,
    )


def _fresh(
    height: int | None,
    lag: int | None,
    verdict: str,
    cohort: int | None = 100,
) -> Freshness:
    return Freshness(
        height=height,
        height_hex=None if height is None else hex(height),
        lag_blocks=lag,
        lag_s=None if lag is None else lag * 12.0,
        verdict=verdict,
        cohort_height=cohort,
    )


def _agree(
    digest: str,
    pin: int = 100,
    verdict: str = "agree",
    canon: str | None = None,
) -> Consistency:
    return Consistency(
        hash=digest,
        number=pin,
        verdict=verdict,
        pin_height=pin,
        canonical_hash=canon if canon is not None else digest,
    )


def _outcome(
    name: str,
    samples: tuple[ProbeResult, ...],
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
        samples=3,
        warmup=0,
        timeout=10.0,
        budget=32,
        outcomes=outcomes,
        budget_remaining=20,
    )


def _rank_block(text: str) -> str:
    rest = text.split("Ranking", 1)[1]
    for marker in (
        "\nNotes",
        "\nCoverage",
        "\nReliability",
        "\nComparison",
        "\nMethods",
        "\nTiming",
        "\nTags",
        "\nBurst",
        "\nProviders",
        "\nCapabilities",
    ):
        if marker in rest:
            rest = rest.split(marker, 1)[0]
    return rest


def _rank_rows(text: str) -> list[str]:
    return [
        ln
        for ln in _rank_block(text).splitlines()
        if "│" in ln and not ("name" in ln and "status" in ln)
    ]


def test_rank_fastest_p95_first_failures_last() -> None:
    slow = _outcome("slow", (_ok(40.0), _ok(50.0), _ok(60.0)))
    fast = _outcome("fast", (_ok(10.0), _ok(12.0), _ok(14.0)))
    dead = _outcome("dead", (_fail(), _fail(), _fail()))
    ranked = rank_outcomes(_result(slow, dead, fast))
    assert [o.endpoint.name for o in ranked] == ["fast", "slow", "dead"]
    assert ranked[0].stats.n_ok > 0
    assert ranked[-1].stats.n_ok == 0


def test_report_makes_winner_obvious() -> None:
    result = _result(
        _outcome("slow", (_ok(40.0), _ok(50.0))),
        _outcome("fast", (_ok(10.0), _ok(12.0))),
        _outcome("dead", (_fail("timeout", "took too long"),)),
    )
    text = format_run(result, color=False)
    assert "Summary" in text
    assert "Ranking" in text
    assert "Comparison" not in text
    assert "Providers" not in text
    assert "Capabilities" not in text
    assert "Fastest  fast" in text
    assert "Rank by p95" in text
    assert "size standard" in text
    assert "requests 32" in text
    assert "similar 10%" in text
    assert "Cite      0.2.0  sha=—  family=evm  vantage=—  utc=—" in text
    assert "Ranking  (by p95; similar within 10%; ~ high err, stale, disagree, or miss; failed last)" in text
    assert "Failed   1/3    dead" in text
    summary = text.split("Ranking", 1)[0]
    assert summary.index("fast") < summary.index("Failed")
    first_rank_line = _rank_rows(text)[0]
    assert "fast" in first_rank_line
    assert "jit" in _rank_block(text)
    assert "timeout: took too long" in text
    assert "↳ Next:" not in text
    assert "severity" not in text.lower()
    assert "finding" not in text.lower()
    full = format_run(result, verbose=True, color=False)
    assert "Comparison" in full
    assert "Providers" in full
    assert "Capabilities" in full
    compare = full.split("Comparison", 1)[1]
    assert compare.index("slow") < compare.index("fast") < compare.index("dead")
    assert "p50" in compare and "p95" in compare and "p99" in compare
    assert "jit" in compare
    assert "rps" in compare
    assert "head" in compare and "lag" in compare and "fresh" in compare
    assert "hash" in compare and "match" in compare
    assert "cap" in compare
    assert "yes" in compare
    assert "timeout" in compare
    assert "missed     dead (timeout)" in full
    providers = full.split("Providers", 1)[1]
    assert "url" in providers
    assert "client" in providers
    for outcome in result.outcomes:
        assert outcome.endpoint.url_id not in providers


def test_comparison_table_keeps_failed_rows() -> None:
    text = format_run(
        _result(
            _outcome("alive", (_ok(10.0),)),
            _outcome("dead", (_fail("connection", "refused"),)),
        ),
        verbose=True,
        color=False,
    )
    block = text.split("Comparison", 1)[1]
    assert "alive" in block
    assert "dead" in block
    assert "fail" in block
    assert "connection" in block
    assert "yes" in block


def test_all_failed_has_no_winner() -> None:
    text = format_run(
        _result(_outcome("dead", (_fail(),))),
        color=False,
    )
    assert "Fastest  none  (all endpoints failed)" in text
    assert "connection: refused" in text


def test_verbose_prints_per_sample() -> None:
    result = _result(_outcome("a", (_ok(10.0), _fail("timeout", "slow"))))
    plain = format_run(result, verbose=False, color=False)
    verbose = format_run(result, verbose=True, color=False)
    assert "timeout  slow" not in plain
    assert "timeout  slow" in verbose
    assert "10.0ms" in verbose
    assert "  1  10.0ms" in verbose or "1  10.0ms" in verbose


def test_ties_and_failures_keep_config_order() -> None:
    a = _outcome("a", (_ok(10.0), _ok(10.0)))
    b = _outcome("b", (_ok(10.0), _ok(10.0)))
    dead1 = _outcome("dead1", (_fail(),))
    dead2 = _outcome("dead2", (_fail(),))
    ranked = rank_outcomes(_result(dead1, b, dead2, a))
    assert [o.endpoint.name for o in ranked] == ["b", "a", "dead1", "dead2"]


def test_rank_by_p95_differs_from_mean() -> None:
    # mean favors spiky (33ms); p95 favors smooth (50ms vs 80ms)
    spiky = _outcome("spiky", (_ok(10.0), _ok(10.0), _ok(80.0)))
    smooth = _outcome("smooth", (_ok(50.0), _ok(50.0), _ok(50.0)))
    result = _result(spiky, smooth)
    assert [o.endpoint.name for o in rank_outcomes(result)] == ["smooth", "spiky"]
    assert [o.endpoint.name for o in rank_outcomes(result, rank_by="mean")] == [
        "spiky",
        "smooth",
    ]
    assert [o.endpoint.name for o in rank_outcomes(result, rank_by="rps")] == [
        "spiky",
        "smooth",
    ]
    text = format_run(result, color=False, rank_by="p95")
    first = _rank_rows(text)[0]
    assert "smooth" in first
    assert "dead" not in first


def test_rank_by_throughput_alias() -> None:
    slow = _outcome("slow", (_ok(40.0), _ok(50.0)))
    fast = _outcome("fast", (_ok(10.0), _ok(12.0)))
    ranked = rank_outcomes(_result(slow, fast), rank_by="throughput")
    assert [o.endpoint.name for o in ranked] == ["fast", "slow"]


def test_failed_never_takes_winner_slot() -> None:
    dead = _outcome("dead", (_fail(),))
    ok = _outcome("ok", (_ok(90.0), _ok(91.0)))
    ranked = rank_outcomes(_result(dead, ok))
    assert ranked[0].endpoint.name == "ok"
    text = format_run(_result(dead, ok), color=False)
    assert "Fastest  ok" in text
    rows = _rank_rows(text)
    assert "ok" in rows[0]
    assert "dead" in rows[1]
    assert "—" in rows[1]


def test_partial_success_does_not_outrank_solid() -> None:
    flaky = _outcome("flaky", (_ok(8.0), _fail(), _fail()))
    solid = _outcome("solid", (_ok(20.0), _ok(22.0), _ok(24.0)))
    dead = _outcome("dead", (_fail(),))
    ranked = rank_outcomes(_result(solid, dead, flaky))
    assert [o.endpoint.name for o in ranked] == ["solid", "flaky", "dead"]


def test_color_ok_green_fail_red() -> None:
    result = _result(
        _outcome("oknode", (_ok(10.0),)),
        _outcome("badnode", (_fail(),)),
    )
    text = format_run(result, color=True)
    assert "\033[32moknode" in text or "\033[1;32moknode" in text
    assert "\033[31mbadnode" in text
    assert "\033[32m" in text
    assert "\033[31m" in text


def test_no_ansi_when_no_color_or_not_tty(monkeypatch) -> None:
    result = _result(_outcome("a", (_ok(10.0),)), _outcome("b", (_fail(),)))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert color_enabled(None) is False
    assert "\033[" not in format_run(result, color=None)

    monkeypatch.delenv("NO_COLOR", raising=False)

    import rpcbench.report as report_mod

    class Dummy:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(report_mod.sys, "stdout", Dummy())
    assert color_enabled(None) is False
    assert "\033[" not in format_run(result, color=None)


def test_explicit_color_false_has_no_ansi() -> None:
    result = _result(_outcome("a", (_ok(10.0),)))
    assert "\033[" not in format_run(result, color=False)
    assert "\033[" in format_run(result, color=True)


def test_bimodal_histogram_is_visible() -> None:
    samples = tuple([_ok(20.0)] * 8 + [_ok(800.0)] * 8)
    text = format_run(_result(_outcome("spiky", samples)), verbose=True, color=False)
    providers = text.split("Providers", 1)[1]
    ranking = _rank_block(text)
    assert "<50ms=8" in providers
    assert "<1s=8" in providers
    assert "8 0 0 8 0" not in text
    assert "jit" in ranking


def test_tables_draw_row_and_column_borders() -> None:
    compact = format_run(
        _result(_outcome("a", (_ok(10.0),)), _outcome("b", (_ok(20.0),))),
        color=False,
    )
    ranking = _rank_block(compact)
    assert "┌" in ranking
    assert "│" in ranking
    assert "┼" in ranking
    assert "└" in ranking
    text = format_run(
        _result(_outcome("a", (_ok(10.0),)), _outcome("b", (_ok(20.0),))),
        verbose=True,
        color=False,
    )
    compare = text.split("Comparison", 1)[1].split("Providers", 1)[0]
    providers = text.split("Providers", 1)[1]
    for block in (compare, _rank_block(text), providers):
        assert "┌" in block
        assert "│" in block
        assert "┼" in block
        assert "└" in block


def test_close_p95_is_similar_not_a_false_winner() -> None:
    a = _outcome("a", (_ok(81.0), _ok(81.0)))
    b = _outcome("b", (_ok(84.0), _ok(84.0)))
    result = _result(a, b)
    placed = place_outcomes(result)
    assert [row.rank for row in placed] == [1, 1]
    assert all(row.similar for row in placed)
    text = format_run(result, color=False)
    assert "Fastest  a, b" in text
    assert "similar within 10% p95" in text
    ranking = _rank_block(text)
    rows = _rank_rows(text)
    assert len(rows) == 2
    assert all("1" in ln for ln in rows)


def test_far_p95_gets_distinct_places() -> None:
    slow = _outcome("slow", (_ok(200.0), _ok(200.0)))
    fast = _outcome("fast", (_ok(81.0), _ok(81.0)))
    placed = place_outcomes(_result(slow, fast))
    assert [row.outcome.endpoint.name for row in placed] == ["fast", "slow"]
    assert [row.rank for row in placed] == [1, 2]
    assert not any(row.similar for row in placed)


def test_high_error_does_not_take_a_place() -> None:
    merkle = _outcome("merkle", tuple([_ok(8.0)] * 2 + [_fail("http_4xx", "no")] * 8))
    solid = _outcome("solid", tuple([_ok(20.0)] * 10))
    placed = place_outcomes(_result(merkle, solid))
    assert [row.outcome.endpoint.name for row in placed] == ["solid", "merkle"]
    assert placed[0].rank == 1
    assert placed[1].rank is None
    assert placed[1].reliable is False
    text = format_run(_result(merkle, solid), color=False)
    assert "Fastest  solid" in text
    ranking = _rank_block(text)
    assert "~" in ranking
    assert "merkle" in ranking


def test_stale_cannot_be_fastest() -> None:
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
    result = _result(lagged, tip)
    placed = place_outcomes(result)
    assert [row.outcome.endpoint.name for row in placed] == ["tip", "lagged"]
    assert placed[0].rank == 1
    assert placed[1].rank is None
    assert placed[1].reliable is False
    text = format_run(result, color=False)
    assert "Fastest  tip" in text
    assert "Stale    1/2    lagged" in text
    ranking = _rank_block(text)
    assert "~" in ranking
    assert "lagged" in ranking
    assert "stale  ~36s" in ranking
    assert "~36s" in text
    full = format_run(result, verbose=True, color=False)
    compare = full.split("Comparison", 1)[1]
    assert "stale" in compare
    assert "97" in full
    assert "stale >2 blocks vs cohort median" in full


def test_disagree_cannot_be_fastest() -> None:
    digest_a = "0x" + "aa" * 32
    digest_b = "0x" + "bb" * 32
    wrong = _outcome(
        "wrong",
        (_ok(8.0), _ok(8.0)),
        consistency=_agree(digest_b, verdict="disagree", canon=digest_a),
    )
    right = _outcome(
        "right",
        (_ok(20.0), _ok(20.0)),
        consistency=_agree(digest_a, verdict="agree", canon=digest_a),
    )
    result = _result(wrong, right)
    placed = place_outcomes(result)
    assert [row.outcome.endpoint.name for row in placed] == ["right", "wrong"]
    assert placed[0].rank == 1
    assert placed[1].rank is None
    assert placed[1].reliable is False
    text = format_run(result, color=False)
    assert "Fastest  right" in text
    assert "Disagree 1/2    wrong" in text
    ranking = _rank_block(text)
    assert "~" in ranking
    assert "wrong" in ranking
    assert "disagree" in ranking
    full = format_run(result, verbose=True, color=False)
    compare = full.split("Comparison", 1)[1]
    assert "no" in compare
    assert digest_b[:10] in full


def test_p99_flagged_when_n_too_small() -> None:
    text = format_run(_result(_outcome("a", (_ok(10.0), _ok(12.0)))), color=False)
    assert "need ≥100" in text
    assert "P99 is the slowest sample until n≥100" in text
    hundred = _outcome("big", tuple(_ok(10.0) for _ in range(100)))
    text_ok = format_run(_result(hundred), color=False)
    assert "need ≥100" not in text_ok


def test_burst_section_and_provider_phases() -> None:
    samples = (_ok(10.0), _ok(12.0), _fail("rate_limit", "Too Many Requests"), _ok(11.0))
    outcome = EndpointOutcome(
        endpoint=Endpoint(name="node", url="http://127.0.0.1/node"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        burst_stats=summarize(samples[:2]),
        steady_stats=summarize(samples[2:]),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=4,
        warmup=0,
        timeout=10.0,
        budget=32,
        outcomes=(outcome,),
        budget_remaining=20,
        burst=2,
        rps=2.0,
    )
    text = format_run(result, color=False)
    assert "burst=2" in text
    assert "rps=2" in text
    assert "Burst  (" not in text
    assert "rate_limit=1" in text
    assert "  node  rate_limit=1" in text
    full = format_run(result, verbose=True, color=False)
    assert "Burst  (first 2 timed samples overlap; then cap 2/s; same request budget; tag throttles as tags=N)" in full
    assert "burst" in full.split("Burst", 1)[1].split("Providers", 1)[0]
    assert "steady" in full.split("Burst", 1)[1]
    assert "rate_limit is 429 / CU throttle" in full
    assert "finding" not in text.lower()


def test_burst_table_shows_tag_rate_limits() -> None:
    samples = (_ok(10.0), _ok(12.0), _ok(11.0))
    tags = tuple(
        TagSnapshot(
            tag=name,
            latency_ms=1000.0,
            height=None,
            hash=None,
            freshness=None,
            skipped=True,
            skip_reason="rate_limit",
        )
        for name in ("latest", "safe", "finalized")
    )
    outcome = EndpointOutcome(
        endpoint=Endpoint(name="merkle", url="http://127.0.0.1/merkle"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        tags=tags,
        burst_stats=summarize(samples),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=3,
        warmup=0,
        timeout=10.0,
        budget=32,
        outcomes=(outcome,),
        budget_remaining=20,
        burst=3,
    )
    text = format_run(result, color=False)
    assert "Burst  (" not in text
    assert "  merkle  tags=3" in text
    assert "tags=3" in text
    assert "finding" not in text.lower()
    full = format_run(result, verbose=True, color=False)
    burst = full.split("Burst", 1)[1].split("Providers", 1)[0]
    assert "err=0%" in burst or "  0%" in burst
    assert "tags=3" in burst


def test_compact_default_omits_detail_tables() -> None:
    outcomes = tuple(
        _outcome(f"n{i}", (_ok(10.0 + i), _ok(12.0 + i))) for i in range(9)
    )
    text = format_run(_result(*outcomes), color=False)
    assert "Fastest" in text
    assert "Ranking" in text
    assert "Comparison" not in text
    assert "Providers" not in text
    assert "Capabilities" not in text
    assert "Coverage" not in text
    assert "Reliability" not in text
    assert "Timing" not in text
    assert "Tags" not in text
    assert "Burst" not in text
    assert "Methods" not in text
    assert "--verbose for full report" in text
    assert text.count("\n") <= 40
    assert "finding" not in text.lower()
    assert "Cite      " in text
    assert "family=evm" in text


def test_verbose_keeps_full_report() -> None:
    result = _result(
        _outcome("slow", (_ok(40.0), _ok(50.0))),
        _outcome("fast", (_ok(10.0), _ok(12.0))),
    )
    full = format_run(result, verbose=True, color=False)
    assert "Comparison" in full
    assert "Ranking" in full
    assert "Providers" in full
    assert "Capabilities" in full
    assert "Coverage" in full
    assert "Reliability" in full
    assert "--verbose for full report" not in full
    assert "Not an SLA or a security audit" in full
    assert "docs/METHODOLOGY.md" in full
    assert "docs/BOUNDARY.md" in full
