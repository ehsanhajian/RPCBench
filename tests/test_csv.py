from __future__ import annotations

import csv
import io

from rpcbench.config import Endpoint
from rpcbench.csv import COLUMNS, format_csv
from rpcbench.freshness import Freshness
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


def test_csv_has_header_and_one_row_per_provider() -> None:
    text = format_csv(_compare())
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(csv.reader(io.StringIO(text)))[0] == list(COLUMNS)
    assert [row["name"] for row in rows] == ["tip", "lagged", "dead"]
    assert len(rows) == 3
    assert "finding" not in text.lower()
    assert list(COLUMNS)[:8] == [
        "utc",
        "vantage",
        "workload",
        "rank_by",
        "rank",
        "name",
        "ok",
        "responded",
    ]
    assert COLUMNS[-6:] == (
        "http_version",
        "encoding",
        "bytes_in_p95",
        "batch_supported",
        "batch_ms",
        "serial_ms",
    )


def test_csv_core_metrics_parse() -> None:
    rows = {row["name"]: row for row in csv.DictReader(io.StringIO(format_csv(_compare())))}
    tip = rows["tip"]
    assert tip["rank"] == "1"
    assert tip["ok"] == "true"
    assert tip["responded"] == "true"
    assert float(tip["p50_ms"]) == 20.0
    assert float(tip["p95_ms"]) == 20.0
    assert float(tip["p99_ms"]) == 20.0
    assert float(tip["mean_ms"]) == 20.0
    assert float(tip["error_rate"]) == 0.0
    assert float(tip["rps"]) == 50.0
    assert float(tip["score"]) == 100
    assert tip["fresh"] == "yes"
    assert tip["verdict"] == "ready"
    assert tip["primary"] == "true"
    assert tip["utc"] == "2026-08-25T12:00:00Z"
    assert tip["workload"] == "eth_blockNumber"
    lagged = rows["lagged"]
    assert lagged["rank"] == ""
    assert lagged["fresh"] == "stale"
    assert lagged["primary"] == "false"
    dead = rows["dead"]
    assert dead["ok"] == "false"
    assert dead["p95_ms"] == ""
    assert dead["responded"] == "false"
    assert float(dead["error_rate"]) == 1.0


def test_csv_redacts_url_secrets() -> None:
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
    text = format_csv(result)
    assert secret not in text
    assert "apiKey" not in text
    assert "https://" not in text
