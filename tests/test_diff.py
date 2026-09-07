from __future__ import annotations

import json
from pathlib import Path

from rpcbench.config import Endpoint
from rpcbench.diff import (
    DiffError,
    compare_reports,
    format_diff,
    format_diff_md,
    history_files,
    load_report,
    write_history,
)
from rpcbench.freshness import Freshness
from rpcbench.report import format_json, run_to_dict
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


def _fail() -> ProbeResult:
    return ProbeResult(
        ok=False,
        reachable=False,
        latency_ms=5.0,
        result=None,
        error="connection refused",
        error_class="connection",
        attempts=1,
    )


def _fresh(lag: int = 0) -> Freshness:
    height = 100 - lag
    return Freshness(
        height=height,
        height_hex=hex(height),
        lag_blocks=lag,
        lag_s=lag * 12.0,
        verdict="stale" if lag > 2 else "fresh",
        cohort_height=100,
    )


def _outcome(name: str, samples: tuple[ProbeResult, ...], *, lag: int = 0) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name=name, url=f"http://127.0.0.1/{name}"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        freshness=_fresh(lag) if any(hit.ok for hit in samples) else None,
    )


def _result(*outcomes: EndpointOutcome, utc: str = "2026-08-25T12:00:00Z") -> RunResult:
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
        started_at=utc,
        vantage="lab",
        sequence_id="seq00001",
    )


def _pair(fast: float, slow: float) -> RunResult:
    return _result(
        _outcome("publicnode", (_ok(fast), _ok(fast))),
        _outcome("drpc", (_ok(slow), _ok(slow))),
    )


def test_diff_ok_within_similar_band() -> None:
    old = run_to_dict(_pair(80.0, 88.0))
    new = run_to_dict(_pair(84.0, 90.0))
    diff = compare_reports(old, new)
    assert diff.old_primary == "publicnode"
    assert diff.new_primary == "publicnode"
    assert diff.primary_worse is False
    assert diff.failed is False
    assert "FAIL" not in format_diff(diff)
    assert "result    ok" in format_diff(diff)


def test_diff_fails_when_primary_p95_beyond_band() -> None:
    old = run_to_dict(_pair(80.0, 88.0))
    new = run_to_dict(_pair(120.0, 90.0))
    diff = compare_reports(old, new)
    assert diff.primary_worse is True
    assert diff.failed is True
    text = format_diff(diff)
    assert "result    FAIL" in text
    assert "+40.0ms" in text
    assert "got worse beyond the 10% similar-band" in text
    assert "finding" not in text.lower()


def test_diff_winner_change_and_new_signals() -> None:
    old = run_to_dict(_pair(80.0, 88.0))
    new_data = run_to_dict(
        _result(
            _outcome("publicnode", (_fail(), _fail())),
            _outcome("drpc", (_ok(90.0), _ok(90.0))),
        )
    )
    diff = compare_reports(old, new_data)
    assert diff.winner_changed is True
    assert diff.primary_changed is True
    assert diff.primary_worse is True
    assert any(row.name == "publicnode" and row.id for row in diff.new_signals)
    text = format_diff(diff)
    assert "Signals   new publicnode" in text
    assert "finding" not in text.lower()
    md = format_diff_md(diff)
    assert md.startswith("# RPCBench diff")
    table = [line for line in md.splitlines() if line.startswith("|")]
    assert table
    assert len({len(line) for line in table}) == 1
    assert "name" in table[0]
    assert "finding" not in md.lower()


def test_diff_improvement_is_not_fail() -> None:
    old = run_to_dict(_pair(120.0, 130.0))
    new = run_to_dict(_pair(80.0, 90.0))
    diff = compare_reports(old, new)
    assert diff.primary_worse is False
    assert diff.failed is False


def test_history_roundtrip(tmp_path: Path) -> None:
    first = format_json(_pair(80.0, 88.0))
    path = write_history(tmp_path, first)
    assert path.exists()
    loaded = load_report(path)
    assert loaded["tool"] == "rpcbench"
    assert loaded["summary"]["primary"] == "publicnode"

    later = json.loads(first)
    later["watermark"] = dict(later["watermark"] or {})
    later["watermark"]["utc"] = "2026-08-25T13:00:00Z"
    later["sequence_id"] = "seq00002"
    write_history(tmp_path, json.dumps(later, indent=2) + "\n")
    files = history_files(tmp_path)
    assert len(files) >= 2
    old, new = files[-2], files[-1]
    assert load_report(old)["watermark"]["utc"] < load_report(new)["watermark"]["utc"]


def test_load_report_rejects_garbage(tmp_path: Path) -> None:
    path = tmp_path / "nope.json"
    path.write_text("{}", encoding="utf-8")
    try:
        load_report(path)
    except DiffError as exc:
        assert "rpcbench" in str(exc)
    else:
        raise AssertionError("expected DiffError")
