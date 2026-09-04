from __future__ import annotations

import json
import re

import httpx
import pytest

from rpcbench.config import Endpoint, parse_endpoints
from rpcbench.report import format_json, format_run, run_to_dict
from rpcbench.rpc import ProbeResult
from rpcbench.run import EndpointOutcome, RunResult, run_endpoints, summarize
from rpcbench.watermark import (
    DOCS_BOUNDARY,
    DOCS_METHODOLOGY,
    as_dict,
    html_footer,
    utc_stamp,
    vantage_label,
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


def _result(**kwargs: object) -> RunResult:
    outcome = EndpointOutcome(
        endpoint=Endpoint(name="a", url="http://127.0.0.1/a"),
        warmup=(),
        samples=(_ok(10.0),),
        stats=summarize((_ok(10.0),)),
    )
    defaults: dict[str, object] = {
        "method": "eth_blockNumber",
        "params": (),
        "samples": 1,
        "warmup": 0,
        "timeout": 10.0,
        "budget": 8,
        "outcomes": (outcome,),
        "budget_remaining": 7,
    }
    defaults.update(kwargs)
    return RunResult(**defaults)  # type: ignore[arg-type]


def test_watermark_nulls_when_unset() -> None:
    mark = as_dict(_result())
    assert mark["git_sha"] is None
    assert mark["utc"] is None
    assert mark["vantage"] is None
    assert mark["family"] == "evm"
    assert mark["workload"] == "eth_blockNumber"
    assert mark["methodology"] == DOCS_METHODOLOGY
    assert mark["boundary"] == DOCS_BOUNDARY


def test_cite_line_uses_dashes_when_unset() -> None:
    text = format_run(_result(), color=False)
    assert "Cite      0.3.0  sha=—  family=evm  vantage=—  utc=—" in text
    assert "methodology" in text
    assert "boundary" in text
    assert "Timing" not in text
    assert "finding" not in text.lower()


def test_verbose_footer_links_docs() -> None:
    full = format_run(_result(), verbose=True, color=False)
    assert DOCS_METHODOLOGY in full
    assert DOCS_BOUNDARY in full
    assert "Not an SLA or a security audit" in full
    assert "finding" not in full.lower()


def test_html_footer_links_both_docs() -> None:
    html = html_footer(
        _result(
            git_sha="deadbeef0001",
            started_at="2026-08-25T12:00:00Z",
            vantage="lab",
        )
    )
    assert DOCS_METHODOLOGY in html
    assert DOCS_BOUNDARY in html
    assert 'href="' in html
    assert "deadbeef0001" in html
    assert "lab" in html
    assert "finding" not in html.lower()


def test_html_footer_nulls_when_unset() -> None:
    html = html_footer(_result())
    assert "sha=—" in html
    assert "vantage=—" in html
    assert DOCS_METHODOLOGY in html
    assert DOCS_BOUNDARY in html


def test_utc_stamp_is_iso_z() -> None:
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", utc_stamp())


def test_vantage_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPCBENCH_VANTAGE", "eu-west-lab")
    assert vantage_label() == "eu-west-lab"


def test_run_endpoints_fills_watermark(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"})

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:8545"}]}
    )
    monkeypatch.setenv("RPCBENCH_VANTAGE", "ci")
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=8,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert result.git_sha
    assert result.started_at
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result.started_at)
    assert result.vantage == "ci"
    assert result.family == "evm"
    data = run_to_dict(result)
    mark = data["watermark"]
    assert mark["git_sha"] == result.git_sha
    assert mark["utc"] == result.started_at
    assert mark["vantage"] == "ci"
    assert mark["workload"] == "eth_blockNumber"
    assert mark["budget"] == "standard"
    blob = format_json(result)
    assert "finding" not in blob.lower()


def test_watermark_does_not_include_url_secrets() -> None:
    secret = "query_secret"
    outcome = EndpointOutcome(
        endpoint=Endpoint(
            name="paid",
            url="https://rpc.example/v3/abcdabcdabcdabcdabcdabcdabcdabcd"
            f"?apiKey={secret}",
        ),
        warmup=(),
        samples=(_ok(10.0),),
        stats=summarize((_ok(10.0),)),
    )
    result = RunResult(
        method="eth_blockNumber",
        params=(),
        samples=1,
        warmup=0,
        timeout=10.0,
        budget=8,
        outcomes=(outcome,),
        budget_remaining=7,
        git_sha="abc",
        started_at="2026-08-25T12:00:00Z",
        vantage="lab",
    )
    mark = json.dumps(as_dict(result))
    html = html_footer(result)
    text = format_run(result, color=False)
    for blob in (mark, html, text):
        assert secret not in blob
        assert "abcdabcdabcdabcdabcdabcdabcdabcd" not in blob
        assert "finding" not in blob.lower()
