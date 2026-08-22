from __future__ import annotations

from rpcbench.config import Endpoint
from rpcbench.report import color_enabled, format_run, place_outcomes, rank_outcomes
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, summarize


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


def _outcome(name: str, samples: tuple[ProbeResult, ...]) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name=name, url=f"http://127.0.0.1/{name}"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
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
    assert "Comparison" in text
    assert "Ranking" in text
    assert "Providers" in text
    assert "Capabilities" in text
    assert "Fastest  fast" in text
    assert "Rank by p95" in text
    assert "size standard" in text
    assert "requests 32" in text
    assert "similar 10%" in text
    assert "Ranking  (by p95; similar within 10%; ~ high err; failed last)" in text
    assert "Failed   1/3    dead" in text
    compare = text.split("Comparison", 1)[1].split("Ranking", 1)[0]
    assert compare.index("slow") < compare.index("fast") < compare.index("dead")
    assert "p50" in compare and "p95" in compare and "p99" in compare
    assert "jit" in compare
    assert "rps" in compare
    assert "cap" in compare
    assert "yes" in compare
    assert "timeout" in compare
    summary, ranking, _ = text.split("Ranking", 1)[0], text.split("Ranking", 1)[1], None
    assert summary.index("fast") < summary.index("Failed")
    first_rank_line = [ln for ln in ranking.splitlines() if ln.strip()][1]
    assert "fast" in first_rank_line
    assert "jitter=" in first_rank_line
    assert "timeout: took too long" in text
    assert "missed     dead (timeout)" in text
    assert "↳ Next:" not in text
    assert "severity" not in text.lower()
    assert "finding" not in text.lower()
    assert "id=" in text


def test_comparison_table_keeps_failed_rows() -> None:
    text = format_run(
        _result(
            _outcome("alive", (_ok(10.0),)),
            _outcome("dead", (_fail("connection", "refused"),)),
        ),
        color=False,
    )
    block = text.split("Comparison", 1)[1].split("Ranking", 1)[0]
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
    ranking = text.split("Ranking", 1)[1]
    first = [ln for ln in ranking.splitlines() if ln.strip()][1]
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
    ranking = text.split("Ranking", 1)[1]
    lines = [ln for ln in ranking.splitlines() if ln.strip()]
    assert "ok" in lines[1]
    assert "dead" in lines[2]
    assert lines[2].strip().startswith("—") or "  —" in lines[2]


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
    text = format_run(_result(_outcome("spiky", samples)), color=False)
    assert "jitter=" in text
    assert "hist  <50ms=8  <100ms=0  <250ms=0  <1s=8  ≥1s=0" in text
    providers = text.split("Providers", 1)[1]
    ranking = text.split("Ranking", 1)[1].split("Providers", 1)[0]
    assert "jitter=" in ranking
    assert "<50ms=8" in providers
    assert "<50ms=8" in providers
    assert "<1s=8" in providers
    assert "<100ms=0" in providers


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
    ranking = text.split("Ranking", 1)[1].split("Providers", 1)[0]
    assert ranking.count("  1  ") >= 2


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
    ranking = text.split("Ranking", 1)[1].split("Providers", 1)[0]
    assert "~" in ranking
    assert "merkle" in ranking


def test_p99_flagged_when_n_too_small() -> None:
    text = format_run(_result(_outcome("a", (_ok(10.0), _ok(12.0)))), color=False)
    assert "need ≥100" in text
    assert "P99 is the slowest sample until n≥100" in text
    hundred = _outcome("big", tuple(_ok(10.0) for _ in range(100)))
    text_ok = format_run(_result(hundred), color=False)
    assert "need ≥100" not in text_ok.split("Providers", 1)[1]
