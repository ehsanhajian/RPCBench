from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from rpcbench.cli import build_parser, main
from rpcbench.config import Endpoint
from rpcbench.freshness import Freshness
from rpcbench.rpc import ProbeResult, make_client
from rpcbench.run import EndpointOutcome, RunResult, summarize
from rpcbench.slo import evaluate_slo, format_slo_failures


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
        error="boom",
        error_class="timeout",
        attempts=1,
    )


def _fresh(lag: int | None) -> Freshness | None:
    if lag is None:
        return None
    return Freshness(
        height=100 - lag,
        height_hex=hex(100 - lag),
        lag_blocks=lag,
        lag_s=float(lag) * 12.0,
        verdict="fresh" if lag <= 2 else "stale",
        cohort_height=100,
    )


def _outcome(
    name: str,
    samples: tuple[ProbeResult, ...],
    *,
    lag: int | None = 0,
) -> EndpointOutcome:
    return EndpointOutcome(
        endpoint=Endpoint(name=name, url=f"http://127.0.0.1/{name}"),
        warmup=(),
        samples=samples,
        stats=summarize(samples),
        freshness=_fresh(lag),
    )


def _result(*outcomes: EndpointOutcome) -> RunResult:
    return RunResult(
        method="eth_blockNumber",
        params=(),
        samples=2,
        warmup=0,
        timeout=5.0,
        budget=32,
        outcomes=outcomes,
        budget_remaining=16,
    )


def test_slo_passes_when_within_budgets() -> None:
    report = evaluate_slo(
        _result(_outcome("a", (_ok(40.0), _ok(42.0)), lag=1)),
        max_p95_ms=50.0,
        max_error_rate=0.1,
        max_lag_blocks=2,
    )
    assert report.ok


def test_slo_fails_max_p95() -> None:
    report = evaluate_slo(
        _result(_outcome("slow", (_ok(80.0), _ok(90.0)))),
        max_p95_ms=50.0,
    )
    assert not report.ok
    assert report.misses[0].budget == "p95"
    assert "slow" in format_slo_failures(report)


def test_slo_fails_error_rate_and_lag() -> None:
    report = evaluate_slo(
        _result(_outcome("bad", (_ok(10.0), _fail()), lag=5)),
        max_error_rate=0.1,
        max_lag_blocks=2,
    )
    assert {m.budget for m in report.misses} == {"error_rate", "lag"}


def test_slo_endpoint_filter() -> None:
    report = evaluate_slo(
        _result(
            _outcome("ok", (_ok(10.0), _ok(12.0))),
            _outcome("slow", (_ok(200.0), _ok(210.0))),
        ),
        max_p95_ms=50.0,
        endpoint="ok",
    )
    assert report.ok
    assert report.checked == ("ok",)


def _mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ident = payload.get("id", 1)
        method = payload.get("method")
        if method == "eth_chainId":
            result: object = "0x1"
        elif method == "eth_blockNumber":
            result = "0x64"
        elif method == "eth_getBlockByNumber":
            result = {
                "number": "0x64",
                "hash": "0x" + "ab" * 32,
                "parentHash": "0x" + "cd" * 32,
            }
        else:
            result = "0x0"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": ident, "result": result}
        )

    return httpx.MockTransport(handler)


def test_cli_ci_max_p95_exits_nonzero(tmp_path: Path, capsys, monkeypatch) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )
    transport = _mock_transport()

    def fake_client(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5.0))

    monkeypatch.setattr("rpcbench.run.make_client", fake_client)
    monkeypatch.setattr("rpcbench.rpc.make_client", fake_client)
    code = main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_blockNumber",
            "--samples",
            "1",
            "--warmup",
            "0",
            "--ci",
            "--max-p95",
            "0.0001",
        ]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "SLO  failed" in err
    assert "p95" in err


def test_cli_passing_slo_still_writes_reports(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    transport = _mock_transport()

    def fake_client(**kwargs):
        return httpx.Client(transport=transport, timeout=5.0)

    monkeypatch.setattr("rpcbench.run.make_client", fake_client)
    monkeypatch.setattr("rpcbench.rpc.make_client", fake_client)
    code = main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_blockNumber",
            "--samples",
            "1",
            "--warmup",
            "0",
            "--ci",
            "--max-p95",
            "100000",
            "--out-dir",
            str(out),
        ]
    )
    assert code == 0
    assert (out / "report.html").is_file()
    assert "<!DOCTYPE html>" in (out / "report.html").read_text(encoding="utf-8")
    assert "SLO  ok" in capsys.readouterr().err


def test_cli_ci_without_budgets_is_usage_error(capsys) -> None:
    code = main(["compare", "--endpoints", "x.yaml", "--ci"])
    assert code == 2
    assert "--max-p95" in capsys.readouterr().err


def test_out_dir_writes_html_json_md(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )
    out = tmp_path / "reports"
    transport = _mock_transport()

    def fake_client(**kwargs):
        return httpx.Client(transport=transport, timeout=5.0)

    monkeypatch.setattr("rpcbench.run.make_client", fake_client)
    monkeypatch.setattr("rpcbench.rpc.make_client", fake_client)
    code = main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_blockNumber",
            "--samples",
            "1",
            "--warmup",
            "0",
            "--out-dir",
            str(out),
        ]
    )
    assert code == 0
    assert (out / "report.json").is_file()
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "RPCBench" in html
    assert (out / "report.md").is_file()


def test_cli_flags_parse() -> None:
    ns = build_parser().parse_args(
        [
            "compare",
            "--endpoints",
            "x.yaml",
            "--ci",
            "--max-p95",
            "100",
            "--max-error-rate",
            "0.05",
            "--max-lag",
            "3",
            "--out-dir",
            "out",
        ]
    )
    assert ns.ci is True
    assert ns.max_p95 == 100.0
    assert ns.max_error_rate == 0.05
    assert ns.max_lag == 3.0
    assert ns.out_dir == "out"
    ns2 = build_parser().parse_args(["compare", "--endpoints", "x.yaml", "--strict"])
    assert ns2.ci is True


def test_action_yml_and_dockerfile_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "action.yml").read_text(encoding="utf-8")
    assert "max-p95" in text
    assert "output-dir" in text
    assert "--ci" in text
    assert (root / "Dockerfile").is_file()
