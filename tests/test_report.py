from __future__ import annotations

from rpcbench.config import Endpoint
from rpcbench.report import color_enabled, format_run, rank_outcomes
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


def test_rank_fastest_mean_first_failures_last() -> None:
    slow = _outcome("slow", (_ok(40.0), _ok(50.0), _ok(60.0)))
    fast = _outcome("fast", (_ok(10.0), _ok(12.0), _ok(14.0)))
    dead = _outcome("dead", (_fail(), _fail(), _fail()))
    ranked = rank_outcomes(_result(slow, dead, fast))
    assert [o.endpoint.name for o in ranked] == ["fast", "slow", "dead"]


def test_report_makes_winner_obvious() -> None:
    result = _result(
        _outcome("slow", (_ok(40.0), _ok(50.0))),
        _outcome("fast", (_ok(10.0), _ok(12.0))),
        _outcome("dead", (_fail("timeout", "took too long"),)),
    )
    text = format_run(result, color=False)
    assert "Summary" in text
    assert "Ranking" in text
    assert "Providers" in text
    assert "Capabilities" in text
    assert "Fastest  fast" in text
    assert "Failed   1/3    dead" in text
    summary, ranking, _ = text.split("Ranking", 1)[0], text.split("Ranking", 1)[1], None
    assert summary.index("fast") < summary.index("Failed")
    first_rank_line = [ln for ln in ranking.splitlines() if ln.strip()][1]
    assert "fast" in first_rank_line
    assert "timeout: took too long" in text
    assert "missed     dead (timeout)" in text
    assert "↳ Next:" not in text
    assert "severity" not in text.lower()
    assert "finding" not in text.lower()
    assert "id=" in text


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


def test_partial_success_sorts_with_ok_group() -> None:
    flaky = _outcome("flaky", (_ok(8.0), _fail(), _fail()))
    solid = _outcome("solid", (_ok(20.0), _ok(22.0), _ok(24.0)))
    dead = _outcome("dead", (_fail(),))
    ranked = rank_outcomes(_result(solid, dead, flaky))
    assert [o.endpoint.name for o in ranked] == ["flaky", "solid", "dead"]


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
