from __future__ import annotations

import json
from pathlib import Path

import pytest

from rpcbench.config import Endpoint
from rpcbench.merge import MergeError, format_merge, merge_reports
from rpcbench.html import format_html
from rpcbench.report import run_to_dict
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, summarize
from rpcbench.vantage import VantageInfo, format_vantage_bits, resolve_vantage


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


def _outcome(name: str, samples: tuple[ProbeResult, ...]) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name=name, url=f"http://127.0.0.1/{name}"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
    )


def _result(
    *outcomes: EndpointOutcome,
    vantage: str = "lab",
    info: VantageInfo | None = None,
    seed: int = 7,
) -> RunResult:
    return RunResult(
        method="eth_blockNumber",
        params=(),
        samples=2,
        warmup=0,
        timeout=10.0,
        budget=16,
        outcomes=outcomes,
        budget_remaining=10,
        seed=seed,
        git_sha="deadbeef0001",
        started_at="2026-08-25T12:00:00Z",
        vantage=vantage,
        vantage_info=info
        or VantageInfo(label=vantage, region=None, city=None, asn=None, hostname="host"),
    )


def test_resolve_vantage_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPCBENCH_VANTAGE", "eu-lab")
    monkeypatch.setenv("RPCBENCH_REGION", "eu-west-1")
    monkeypatch.setenv("RPCBENCH_CITY", "Dublin")
    monkeypatch.setenv("RPCBENCH_ASN", "AS16509")
    info = resolve_vantage()
    assert info.label == "eu-lab"
    assert info.region == "eu-west-1"
    assert info.city == "Dublin"
    assert info.asn == "AS16509"
    assert info.hostname


def test_watermark_includes_vantage_meta() -> None:
    info = VantageInfo(
        label="eu-lab",
        region="eu-west-1",
        city="Dublin",
        asn="AS16509",
        hostname="box-1",
    )
    result = _result(
        _outcome("a", (_ok(10.0), _ok(12.0))),
        vantage="eu-lab",
        info=info,
    )
    data = run_to_dict(result)
    mark = data["watermark"]
    assert mark["vantage"] == "eu-lab"
    assert mark["region"] == "eu-west-1"
    assert mark["city"] == "Dublin"
    assert mark["asn"] == "AS16509"
    assert mark["hostname"] == "box-1"
    assert mark["vantage_meta"]["label"] == "eu-lab"
    from rpcbench.watermark import cite_line

    line = cite_line(result)
    assert "vantage=eu-lab" in line
    assert "region=eu-west-1" in line
    assert "city=Dublin" in line


def test_html_shows_vantage_metadata() -> None:
    info = VantageInfo(
        label="us-lab",
        region="us-east-1",
        city="Ashburn",
        asn="AS14618",
        hostname="box-2",
    )
    html = format_html(
        _result(
            _outcome("a", (_ok(20.0), _ok(22.0))),
            vantage="us-lab",
            info=info,
        )
    )
    assert "vantage=us-lab" in html
    assert "region=us-east-1" in html
    assert "city=Ashburn" in html


def test_merge_two_regional_reports() -> None:
    eu = run_to_dict(
        _result(
            _outcome("publicnode", (_ok(40.0), _ok(42.0))),
            _outcome("drpc", (_ok(80.0), _ok(82.0))),
            vantage="eu-west",
            info=VantageInfo(label="eu-west", region="eu-west-1", hostname="eu"),
        )
    )
    us = run_to_dict(
        _result(
            _outcome("publicnode", (_ok(120.0), _ok(122.0))),
            _outcome("drpc", (_ok(90.0), _ok(92.0))),
            vantage="us-east",
            info=VantageInfo(label="us-east", region="us-east-1", hostname="us"),
        )
    )
    merged = merge_reports([eu, us])
    assert len(merged.vantages) == 2
    text = format_merge(merged)
    assert "Per region" in text
    assert "eu-west" in text
    assert "us-east" in text
    assert "Global" in text
    assert "browser" in text.lower() or "RUM" in text or "not a browser" in text
    blob = merged.as_dict()
    assert blob["kind"] == "merge"
    assert "publicnode" in blob["regions"]
    assert blob["regions"]["publicnode"]["eu-west"]["p95_ms"] is not None
    # mean of ~41 and ~121
    global_names = [row["name"] for row in blob["global"]["ranking"]]
    assert set(global_names) == {"publicnode", "drpc"}


def test_merge_rejects_seed_mismatch() -> None:
    a = run_to_dict(
        _result(_outcome("a", (_ok(10.0),)), vantage="eu", seed=1)
    )
    b = run_to_dict(
        _result(_outcome("a", (_ok(10.0),)), vantage="us", seed=2)
    )
    with pytest.raises(MergeError, match="seed"):
        merge_reports([a, b])


def test_cli_merge(tmp_path: Path, capsys) -> None:
    from rpcbench.cli import main

    eu = run_to_dict(
        _result(
            _outcome("publicnode", (_ok(40.0), _ok(42.0))),
            vantage="eu-west",
        )
    )
    us = run_to_dict(
        _result(
            _outcome("publicnode", (_ok(100.0), _ok(102.0))),
            vantage="us-east",
        )
    )
    p1 = tmp_path / "eu.json"
    p2 = tmp_path / "us.json"
    p1.write_text(json.dumps(eu), encoding="utf-8")
    p2.write_text(json.dumps(us), encoding="utf-8")
    code = main(["merge", str(p1), str(p2)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Merge" in out
    assert "eu-west" in out
    assert "us-east" in out


def test_format_vantage_bits_skips_empty() -> None:
    assert format_vantage_bits(VantageInfo(label="x")) == ""
    assert "region=eu" in format_vantage_bits(
        VantageInfo(label="x", region="eu")
    )
