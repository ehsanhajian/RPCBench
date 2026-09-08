from __future__ import annotations

import csv
import io

import pytest

from rpcbench.config import Endpoint
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.markdown import format_md
from rpcbench.report import format_run, run_to_dict
from rpcbench.rpc import ProbeResult
from rpcbench.run import BatchSummary, EndpointOutcome, RunResult, summarize, summarize_transport


def _hit() -> ProbeResult:
    return ProbeResult(
        ok=True,
        reachable=True,
        latency_ms=12.0,
        result="0x1",
        error=None,
        error_class=None,
        attempts=1,
        method="eth_blockNumber",
        http_version="1.1",
        encoding="gzip",
        bytes_out=64,
        bytes_in=82,
    )


def _result(*, batch: BatchSummary | None, size: int = 0) -> RunResult:
    samples = (_hit(), _hit())
    return RunResult(
        method="eth_blockNumber",
        params=(),
        samples=2,
        warmup=0,
        timeout=10.0,
        budget=32,
        outcomes=(
            EndpointOutcome(
                endpoint=Endpoint(name="local", url="http://127.0.0.1/local"),
                warmup=(),
                samples=samples,
                stats=summarize(samples),
                transport=summarize_transport(samples),
                batch=batch,
            ),
        ),
        budget_remaining=20,
        batch=size,
    )


def test_batch_and_p95_match_across_cli_html_json_csv_md() -> None:
    batch = BatchSummary(
        size=3,
        method="eth_blockNumber",
        supported=True,
        partial=False,
        batch_ms=12.0,
        serial_ms=40.0,
        ratio=40.0 / 12.0,
        n_ok=3,
        n_fail=0,
        error=None,
        error_class=None,
    )
    result = _result(batch=batch, size=3)
    data = run_to_dict(result)
    row = data["ranking"][0]
    p95 = row["p95_ms"]
    blob = row["batch"]
    assert blob is not None
    assert blob["batch_ms"] == 12.0
    assert blob["serial_ms"] == 40.0
    assert blob["ratio"] == 40.0 / 12.0
    assert blob["supported"] is True

    cli = format_run(result, color=False)
    html = format_html(result)
    md = format_md(result)
    csv_row = next(csv.DictReader(io.StringIO(format_csv(result))))

    assert f"{p95:.1f}ms" in cli
    assert f"{p95:.1f}ms" in html
    assert f"{p95:.1f}ms" in md
    assert float(csv_row["p95_ms"]) == p95

    cli_batch = cli.split("Batch", 1)[1]
    md_batch = md.split("## Batch", 1)[1]
    assert "12.0ms" in cli_batch
    assert "40.0ms" in cli_batch
    assert "3.3×" in cli_batch
    assert "yes" in cli_batch
    assert "12.0ms" in html
    assert "40.0ms" in html
    assert "3.3×" in html
    assert "12.0ms" in md_batch
    assert "40.0ms" in md_batch
    assert "3.3×" in md_batch
    assert "yes" in md_batch
    assert float(csv_row["batch_ms"]) == 12.0
    assert float(csv_row["serial_ms"]) == 40.0
    assert float(csv_row["batch_ratio"]) == pytest.approx(40.0 / 12.0, rel=1e-5)
    assert csv_row["batch_supported"] == "true"

    assert csv_row["http_version"] == "1.1"
    assert csv_row["encoding"] == "gzip"
    assert "## Transport" in md
    assert "1.1" in md.split("## Transport", 1)[1]
    assert "gzip" in md.split("## Transport", 1)[1]
    assert "Transport" in html
    assert "finding" not in cli.lower()
    assert "finding" not in html.lower()
    assert "finding" not in md.lower()


def test_batch_budget_skip_matches_across_formats() -> None:
    batch = BatchSummary(
        size=3,
        method="eth_blockNumber",
        supported=False,
        partial=False,
        batch_ms=None,
        serial_ms=0.1,
        ratio=None,
        n_ok=0,
        n_fail=3,
        error="request budget exceeded (6)",
        error_class="budget",
    )
    result = _result(batch=batch, size=3)
    cli = format_run(result, color=False).split("Batch", 1)[1]
    html = format_html(result)
    md = format_md(result).split("## Batch", 1)[1]
    csv_row = next(csv.DictReader(io.StringIO(format_csv(result))))
    assert "skip" in cli
    assert "budget" in cli
    assert "skip" in html
    assert "budget" in html
    assert "skip" in md
    assert "budget" in md
    assert csv_row["batch_supported"] == "skip"
    assert csv_row["batch_ms"] == ""
    assert csv_row["batch_ratio"] == ""
