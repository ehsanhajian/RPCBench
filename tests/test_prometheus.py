from __future__ import annotations

from rpcbench.config import Endpoint
from rpcbench.prometheus import format_prometheus, validate_prometheus_text
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
        error="timeout",
        error_class="timeout",
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
        samples=2,
        warmup=0,
        timeout=5.0,
        budget=32,
        outcomes=outcomes,
        budget_remaining=16,
        family="evm",
    )


def test_prometheus_includes_latency_and_error_per_provider() -> None:
    blob = format_prometheus(
        _result(
            _outcome("fast", (_ok(10.0), _ok(12.0))),
            _outcome("slow", (_ok(80.0), _fail())),
        )
    )
    assert 'provider="fast"' in blob
    assert 'provider="slow"' in blob
    assert 'method="eth_blockNumber"' in blob
    assert 'family="evm"' in blob
    assert "rpcbench_latency_ms" in blob
    assert 'quantile="0.95"' in blob
    assert "rpcbench_error_rate" in blob
    assert "rpcbench_rps" in blob
    assert "rpcbench_reliability_score" in blob
    assert "rpcbench_latency_histogram_bucket" in blob
    assert 'le="+Inf"' in blob
    assert validate_prometheus_text(blob) == []


def test_prometheus_escapes_labels() -> None:
    outcome = _outcome('a"b\\c', (_ok(1.0),))
    blob = format_prometheus(_result(outcome))
    assert r'provider="a\"b\\c"' in blob
    assert validate_prometheus_text(blob) == []


def test_prometheus_chain_label_when_payload_has_chain_id() -> None:
    from rpcbench.profile import PayloadMeta

    result = _result(_outcome("local", (_ok(20.0), _ok(22.0))))
    result = RunResult(
        method=result.method,
        params=result.params,
        samples=result.samples,
        warmup=result.warmup,
        timeout=result.timeout,
        budget=result.budget,
        outcomes=result.outcomes,
        budget_remaining=result.budget_remaining,
        family="evm",
        payload=PayloadMeta(source="chain", head=1, chain_id=1),
    )
    blob = format_prometheus(result)
    assert 'chain="1"' in blob


def test_cli_prometheus_stdout(tmp_path, monkeypatch, capsys) -> None:
    import json

    import httpx

    from rpcbench.cli import main

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ident = payload.get("id", 1)
        method = payload.get("method")
        if method == "eth_chainId":
            result: object = "0x1"
        elif method == "eth_blockNumber":
            result = "0x10"
        elif method == "eth_getBlockByNumber":
            result = {
                "number": "0x10",
                "hash": "0x" + "11" * 32,
                "parentHash": "0x" + "22" * 32,
            }
        else:
            result = "0x0"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": ident, "result": result}
        )

    transport = httpx.MockTransport(handler)

    def fake_client(**kwargs):
        return httpx.Client(transport=transport, timeout=5.0)

    monkeypatch.setattr("rpcbench.run.make_client", fake_client)
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
            "--prometheus",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "rpcbench_latency_ms" in out
    assert validate_prometheus_text(out) == []


def test_out_dir_writes_metrics_prom(tmp_path, monkeypatch) -> None:
    import json

    import httpx

    from rpcbench.cli import main

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )
    out = tmp_path / "reports"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ident = payload.get("id", 1)
        method = payload.get("method")
        if method == "eth_chainId":
            result: object = "0x1"
        elif method == "eth_blockNumber":
            result = "0x10"
        elif method == "eth_getBlockByNumber":
            result = {
                "number": "0x10",
                "hash": "0x" + "11" * 32,
                "parentHash": "0x" + "22" * 32,
            }
        else:
            result = "0x0"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": ident, "result": result}
        )

    transport = httpx.MockTransport(handler)

    def fake_client(**kwargs):
        return httpx.Client(transport=transport, timeout=5.0)

    monkeypatch.setattr("rpcbench.run.make_client", fake_client)
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
    prom = (out / "metrics.prom").read_text(encoding="utf-8")
    assert validate_prometheus_text(prom) == []


def test_cli_rejects_prometheus_with_json(capsys) -> None:
    from rpcbench.cli import main

    code = main(
        ["compare", "--endpoints", "x.yaml", "--prometheus", "--json"]
    )
    assert code == 2
    assert "--prometheus" in capsys.readouterr().err
