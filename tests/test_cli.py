from __future__ import annotations

from pathlib import Path

from rpcbench.cli import build_parser, main
from rpcbench import __version__


def test_cli_version(capsys) -> None:
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out
    assert "0.3.0" in out


def test_cli_missing_file(capsys) -> None:
    code = main(["run", "--endpoints", "/no/such/endpoints.yaml"])
    assert code == 2
    err = capsys.readouterr().err
    assert "not found" in err


def test_cli_defaults() -> None:
    ns = build_parser().parse_args(["run", "--endpoints", "x.yaml"])
    assert ns.samples is None
    assert ns.warmup is None
    assert ns.max_requests == 128
    assert ns.sample_budget == "standard"
    assert ns.method is None
    assert ns.preset is None
    assert ns.verbose is False
    assert ns.allow_writes is False
    assert ns.concurrency is None
    assert ns.sequential is False
    assert ns.seed == 0
    assert ns.json is False
    assert ns.html is False
    assert ns.md is False
    assert ns.csv is False
    assert ns.history is None
    assert ns.output is None
    assert ns.rank_by == "p95"
    assert ns.similar_band == 0.10
    assert ns.stale_blocks == 2
    assert ns.block_time is None
    assert ns.block is None
    assert ns.profile is None
    assert ns.timeout is None
    assert ns.max_duration is None
    assert ns.burst == 0
    assert ns.batch == 0
    assert ns.rps == 0.0
    assert ns.new_connection is False
    assert ns.http2 is False
    assert ns.http1 is False


def test_cli_sample_budget_short() -> None:
    from rpcbench.cli import SAMPLE_BUDGETS, apply_sample_budget

    ns = build_parser().parse_args(
        ["run", "--endpoints", "x.yaml", "--budget", "short"]
    )
    apply_sample_budget(ns)
    assert ns.sample_budget == "short"
    assert ns.samples == SAMPLE_BUDGETS["short"]["samples"]
    assert ns.warmup == 0
    assert ns.timeout == 5.0
    assert ns.max_duration == 30.0
    assert ns.concurrency == 0


def test_cli_sample_budget_long_is_more_samples_only() -> None:
    from rpcbench.cli import apply_sample_budget

    ns = build_parser().parse_args(
        ["run", "--endpoints", "x.yaml", "--budget", "long"]
    )
    apply_sample_budget(ns)
    assert ns.samples == 50
    assert ns.warmup == 2
    assert ns.max_duration == 1800.0


def test_cli_samples_override_budget() -> None:
    from rpcbench.cli import apply_sample_budget

    ns = build_parser().parse_args(
        ["run", "--endpoints", "x.yaml", "--budget", "short", "--samples", "7"]
    )
    apply_sample_budget(ns)
    assert ns.samples == 7
    assert ns.warmup == 0


def test_cli_rejects_numeric_budget(capsys) -> None:
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["run", "--endpoints", "http://127.0.0.1:8545", "--budget", "128"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_cli_short_budget_two_endpoints(tmp_path: Path, monkeypatch, capsys) -> None:
    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:1\n"
        "  - name: b\n    url: http://127.0.0.1:2\n",
        encoding="utf-8",
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(["run", "--endpoints", str(cfg), "--budget", "short"])
    assert code == 0
    assert calls["n"] == 16
    out = capsys.readouterr().out
    assert "size short" in out
    assert "Samples   3 after 0 warmup" in out


def test_cli_run_mixed(tmp_path: Path, monkeypatch, capsys) -> None:
    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: ok\n"
        "    url: http://127.0.0.1:8545\n"
        "  - name: bad\n"
        "    url: http://127.0.0.1:9\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 9:
            raise httpx.ConnectError("refused")
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "ok" in out
    assert "bad" in out
    assert "p95" in out
    assert "err" in out
    assert "Fastest" in out
    assert "Ranking" in out
    assert "Comparison" not in out
    assert "Capabilities" not in out
    assert "Coverage" not in out
    assert "↳ Next:" not in out


def test_cli_preset_balance(tmp_path: Path, monkeypatch, capsys) -> None:
    import json

    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(json.loads(request.content)["method"])
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x0"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--preset",
            "balance",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    assert methods == [
        "eth_getBalance",
        "eth_blockNumber",
        "eth_getBlockByNumber",
        "web3_clientVersion",
        "eth_getBlockByNumber",
        "eth_getBlockByNumber",
        "eth_getBlockByNumber",
    ]
    assert "eth_getBalance" in capsys.readouterr().out


def test_cli_method_flag(tmp_path: Path, monkeypatch) -> None:
    import json

    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(json.loads(request.content)["method"])
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_chainId",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    assert methods == [
        "eth_chainId",
        "eth_blockNumber",
        "eth_getBlockByNumber",
        "web3_clientVersion",
        "eth_getBlockByNumber",
        "eth_getBlockByNumber",
        "eth_getBlockByNumber",
    ]


def test_cli_rejects_write_method(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(
        ["run", "--endpoints", str(cfg), "--method", "eth_sendTransaction"]
    )
    assert code == 2
    assert "write method" in capsys.readouterr().err


def test_cli_rejects_preset_and_method(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--preset",
            "head",
            "--method",
            "eth_chainId",
        ]
    )
    assert code == 2
    assert "either --preset or --method" in capsys.readouterr().err


def test_cli_compare_prints_report(tmp_path: Path, monkeypatch, capsys) -> None:
    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        ["compare", "--endpoints", str(cfg), "--samples", "1", "--warmup", "0"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Fastest  ok" in out
    assert "Ranking" in out
    assert "Verdict" in out
    assert "Primary" in out
    assert "Comparison" not in out
    assert "Signals" not in out


def test_cli_verbose_prints_samples(tmp_path: Path, monkeypatch, capsys) -> None:
    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--samples",
            "2",
            "--warmup",
            "0",
            "--verbose",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "samples" in out
    assert "1" in out
    assert "2" in out
    assert "Comparison" in out
    assert "Reliability" in out
    assert "Signals" in out
    assert "Coverage" in out
    assert "Providers" in out
    assert "Capabilities" in out


def test_cli_compare_url_endpoint(monkeypatch, capsys) -> None:
    import httpx

    from rpcbench import run as run_mod

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "compare",
            "--endpoints",
            "http://127.0.0.1:8545",
            "--samples",
            "1",
            "--warmup",
            "0",
            "--verbose",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "127.0.0.1" in out
    assert "url" in out.split("Providers", 1)[1]


def test_cli_allow_writes(tmp_path: Path, monkeypatch) -> None:
    import json

    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(json.loads(request.content)["method"])
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_sendRawTransaction",
            "--allow-writes",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    assert methods == [
        "eth_sendRawTransaction",
        "eth_blockNumber",
        "eth_getBlockByNumber",
        "web3_clientVersion",
        "eth_getBlockByNumber",
        "eth_getBlockByNumber",
        "eth_getBlockByNumber",
    ]


def test_cli_kill_switch_env(monkeypatch, capsys) -> None:
    monkeypatch.setenv("RPCBENCH_DISABLED", "1")
    code = main(["run", "--endpoints", "http://127.0.0.1:8545"])
    assert code == 2
    assert "disabled" in capsys.readouterr().err


def test_cli_kill_switch_file(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "DISABLED"
    path.write_text("off\n", encoding="utf-8")
    monkeypatch.setenv("RPCBENCH_DISABLE_FILE", str(path))
    code = main(["run", "--endpoints", "http://127.0.0.1:8545"])
    assert code == 2
    assert "disable file" in capsys.readouterr().err


def test_cli_budget_hard_cap(monkeypatch, capsys) -> None:
    monkeypatch.setenv("RPCBENCH_MAX_REQUESTS", "4")
    code = main(
        ["run", "--endpoints", "http://127.0.0.1:8545", "--max-requests", "5"]
    )
    assert code == 2
    assert "hard cap" in capsys.readouterr().err


def test_cli_rejects_negative_concurrency(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(
        ["run", "--endpoints", str(cfg), "--concurrency", "-1", "--samples", "1"]
    )
    assert code == 2
    assert "concurrency" in capsys.readouterr().err


def test_cli_allows_concurrency_wave_cap(tmp_path: Path, monkeypatch, capsys) -> None:
    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--concurrency",
            "8",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    assert "Mode      paired" in capsys.readouterr().out


def test_cli_rejects_negative_stale_blocks(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(
        ["run", "--endpoints", str(cfg), "--stale-blocks", "-1", "--samples", "1"]
    )
    assert code == 2
    assert "stale-blocks" in capsys.readouterr().err


def test_cli_rejects_burst_above_cap(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(["run", "--endpoints", str(cfg), "--burst", "9", "--samples", "1"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--burst" in err


def test_cli_batch_flag_defaults_to_three() -> None:
    ns = build_parser().parse_args(["run", "--endpoints", "x.yaml", "--batch"])
    assert ns.batch == 3
    ns = build_parser().parse_args(["run", "--endpoints", "x.yaml", "--batch", "5"])
    assert ns.batch == 5


def test_cli_rejects_batch_above_cap(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(["run", "--endpoints", str(cfg), "--batch", "9", "--samples", "1"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--batch" in err


def test_cli_rejects_negative_rps(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(["run", "--endpoints", str(cfg), "--rps", "-1", "--samples", "1"])
    assert code == 2
    assert "--rps" in capsys.readouterr().err


def test_cli_rejects_invalid_block_pin(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(["run", "--endpoints", str(cfg), "--block", "nope", "--samples", "1"])
    assert code == 2
    assert "--block" in capsys.readouterr().err


def test_cli_block_pin_is_passed(tmp_path: Path, monkeypatch, capsys) -> None:
    import json

    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    pins: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "eth_getBlockByNumber":
            pins.append(payload["params"][0])
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "number": "0x10",
                        "hash": "0x" + "aa" * 32,
                    },
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--block",
            "0x10",
            "--samples",
            "1",
            "--warmup",
            "0",
            "--verbose",
        ]
    )
    assert code == 0
    assert pins[0] == "0x10"
    assert "latest" in pins
    out = capsys.readouterr().out
    assert "yes" in out
    assert "hash at block 16" in out


def test_cli_json_stdout(tmp_path: Path, monkeypatch, capsys) -> None:
    import json

    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--samples",
            "1",
            "--warmup",
            "0",
            "--json",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert out.lstrip().startswith("{")
    assert "====" not in out
    data = json.loads(out)
    assert data["watermark"]["family"] == "evm"
    assert data["watermark"]["methodology"].endswith("docs/METHODOLOGY.md")
    assert data["watermark"]["boundary"].endswith("docs/BOUNDARY.md")
    assert data["schema"] == 1
    assert data["mode"] == "paired"
    assert data["seed"] == 0
    assert data["sequence_id"]
    assert data["pairs"]
    assert data["summary"]["fastest"] == "ok"
    assert data["providers"][0]["id"]
    assert data["ranking"][0]["name"] == "ok"
    assert data["stale_blocks"] == 2
    assert data["block_time_s"] == 12.0
    assert data["cohort_height"] == 42
    assert data["pin_height"] == 42
    assert data["canonical_hash"] is None
    assert data["summary"]["stale_names"] == []
    assert data["summary"]["disagree_names"] == []
    assert data["comparison"][0]["freshness"]["verdict"] == "fresh"
    assert data["comparison"][0]["freshness"]["height"] == 42
    assert data["providers"][0]["freshness"]["lag_blocks"] == 0
    assert data["comparison"][0]["consistency"]["verdict"] == "unknown"
    assert data["providers"][0]["client"] is None
    assert {row["tag"] for row in data["tags"]} == {"latest", "safe", "finalized"}
    assert all(row["skipped"] for row in data["tags"])


def test_cli_output_file_keeps_table(tmp_path: Path, monkeypatch, capsys) -> None:
    import json

    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--samples",
            "1",
            "--warmup",
            "0",
            "-o",
            str(report),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Fastest  ok" in out
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["summary"]["fastest"] == "ok"
    assert data["capabilities"]["responded"] == 1
    assert data["rank_by"] == "p95"


def test_cli_html_needs_output(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(["run", "--endpoints", str(cfg), "--html"])
    assert code == 2
    assert "--html needs -o FILE" in capsys.readouterr().err


def test_cli_html_writes_file_keeps_table(tmp_path: Path, monkeypatch, capsys) -> None:
    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.html"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--samples",
            "1",
            "--warmup",
            "0",
            "--html",
            "-o",
            str(report),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Fastest  ok" in out
    html = report.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "Ranking" in html
    assert "p95" in html
    assert "<svg" in html
    assert "<script" not in html.lower()
    assert "finding" not in html.lower()
    assert "Heatmap" in html
    assert "Signals" in html
    assert "@media print" in html


def test_cli_md_stdout(tmp_path: Path, monkeypatch, capsys) -> None:
    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    report = tmp_path / "report.md"
    hist = tmp_path / "history"
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--samples",
            "1",
            "--warmup",
            "0",
            "--md",
            "-o",
            str(report),
            "--history",
            str(hist),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("# RPCBench")
    table = [line for line in out.splitlines() if line.startswith("|")]
    assert table
    assert len({len(line) for line in table}) == 1
    assert "p95" in table[0]
    assert "rel" in table[0]
    assert "finding" not in out.lower()
    assert report.read_text(encoding="utf-8") == out
    files = list(hist.glob("*.json"))
    assert len(files) == 1
    assert '"tool": "rpcbench"' in files[0].read_text(encoding="utf-8")


def test_cli_md_rejects_json(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(["run", "--endpoints", str(cfg), "--md", "--json"])
    assert code == 2
    assert "pick --json, --md, or --csv" in capsys.readouterr().err


def test_cli_csv_stdout(tmp_path: Path, monkeypatch, capsys) -> None:
    import csv
    import io

    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    report = tmp_path / "report.csv"
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--samples",
            "1",
            "--warmup",
            "0",
            "--csv",
            "-o",
            str(report),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    rows = list(csv.DictReader(io.StringIO(out)))
    assert rows[0]["name"] == "ok"
    assert "p95_ms" in rows[0]
    assert "error_rate" in rows[0]
    assert "score" in rows[0]
    assert "rank" in rows[0]
    assert "finding" not in out.lower()
    assert report.read_text(encoding="utf-8") == out


def test_cli_output_csv_suffix_keeps_table(tmp_path: Path, monkeypatch, capsys) -> None:
    import csv
    import io

    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    report = tmp_path / "out.csv"
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--samples",
            "1",
            "--warmup",
            "0",
            "-o",
            str(report),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Fastest  ok" in out
    rows = list(csv.DictReader(io.StringIO(report.read_text(encoding="utf-8"))))
    assert rows[0]["name"] == "ok"
    assert rows[0]["responded"] == "true"


def test_cli_diff_exit_codes(tmp_path: Path, capsys) -> None:
    from rpcbench.config import Endpoint
    from rpcbench.freshness import Freshness
    from rpcbench.report import format_json
    from rpcbench.rpc import ProbeResult
    from rpcbench.run import EndpointOutcome, RunResult, summarize

    def ok(ms: float) -> ProbeResult:
        return ProbeResult(
            ok=True,
            reachable=True,
            latency_ms=ms,
            result="0x1",
            error=None,
            error_class=None,
            attempts=1,
        )

    def outcome(name: str, ms: float) -> EndpointOutcome:
        samples = (ok(ms), ok(ms))
        return EndpointOutcome(
            endpoint=Endpoint(name=name, url=f"http://127.0.0.1/{name}"),
            warmup=(),
            samples=samples,
            stats=summarize(samples),
            freshness=Freshness(
                height=100,
                height_hex=hex(100),
                lag_blocks=0,
                lag_s=0.0,
                verdict="fresh",
                cohort_height=100,
            ),
        )

    def pair(fast: float, slow: float) -> RunResult:
        return RunResult(
            method="eth_blockNumber",
            params=(),
            samples=2,
            warmup=0,
            timeout=10.0,
            budget=16,
            outcomes=(outcome("publicnode", fast), outcome("drpc", slow)),
            budget_remaining=10,
        )

    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(format_json(pair(80.0, 88.0)), encoding="utf-8")
    new.write_text(format_json(pair(84.0, 90.0)), encoding="utf-8")
    code = main(["diff", str(old), str(new)])
    out = capsys.readouterr().out
    assert code == 0
    assert "result    ok" in out
    assert "finding" not in out.lower()

    worse = tmp_path / "worse.json"
    worse.write_text(format_json(pair(120.0, 90.0)), encoding="utf-8")
    code = main(["diff", str(old), str(worse)])
    out = capsys.readouterr().out
    assert code == 1
    assert "result    FAIL" in out

    hist = tmp_path / "hist"
    hist.mkdir()
    (hist / "a.json").write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
    (hist / "b.json").write_text(worse.read_text(encoding="utf-8"), encoding="utf-8")
    code = main(["diff", "--history", str(hist)])
    assert code == 1


def test_cli_diff_needs_two_files(capsys) -> None:
    code = main(["diff"])
    assert code == 2
    assert "OLD.json NEW.json" in capsys.readouterr().err


def test_cli_rejects_bad_rank_by(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(["run", "--endpoints", str(cfg), "--rank-by", "latency"])
    assert code == 2
    assert "rank-by" in capsys.readouterr().err


def test_cli_rejects_bad_similar_band(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(["run", "--endpoints", str(cfg), "--similar-band", "2"])
    assert code == 2
    assert "similar-band" in capsys.readouterr().err


def test_cli_rejects_profile_with_method(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(
        ["run", "--endpoints", str(cfg), "--profile", "mix", "--method", "eth_chainId"]
    )
    assert code == 2
    assert "profile" in capsys.readouterr().err


def test_cli_mix_budget_too_low(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:1\n"
        "  - name: b\n    url: http://127.0.0.1:2\n",
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--profile",
            "mix",
            "--samples",
            "1",
            "--warmup",
            "0",
            "--max-requests",
            "4",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "mix needs 22 requests" in err
    assert "--max-requests 22" in err


def test_cli_batch_budget_too_low(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:1\n"
        "  - name: b\n    url: http://127.0.0.1:2\n",
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--samples",
            "1",
            "--warmup",
            "0",
            "--batch",
            "8",
            "--max-requests",
            "10",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "--batch 8 needs" in err
    assert "--max-requests" in err


def test_cli_profile_mix(tmp_path: Path, monkeypatch, capsys) -> None:
    import json

    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(json.loads(request.content)["method"])
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--profile",
            "mix",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Method    mix" in out
    assert "Methods  (per-method" not in out
    assert methods == [
        "eth_blockNumber",
        "eth_chainId",
        "eth_getBlockByNumber",
        "eth_getBalance",
        "eth_call",
        "eth_getLogs",
        "eth_getBlockByNumber",
        "web3_clientVersion",
        "eth_getBlockByNumber",
        "eth_getBlockByNumber",
        "eth_getBlockByNumber",
    ]


def test_cli_long_mix_does_not_add_archive_or_ws(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import json

    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(json.loads(request.content)["method"])
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--budget",
            "long",
            "--profile",
            "mix",
            "--samples",
            "1",
            "--warmup",
            "0",
        ]
    )
    assert code == 0
    assert "eth_getLogs" in methods
    assert "eth_call" in methods
    assert not any("trace" in m or "debug" in m for m in methods)
    assert "eth_getBlockByNumber" in methods
    unique = list(dict.fromkeys(methods))
    assert unique == [
        "eth_blockNumber",
        "eth_chainId",
        "eth_getBlockByNumber",
        "eth_getBalance",
        "eth_call",
        "eth_getLogs",
        "web3_clientVersion",
    ]


def test_cli_rank_by_mean(tmp_path: Path, monkeypatch, capsys) -> None:
    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "compare",
            "--endpoints",
            str(cfg),
            "--samples",
            "1",
            "--warmup",
            "0",
            "--rank-by",
            "mean",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Rank by mean" in out
    assert "Ranking  (by mean; similar within 10%; ~ high err, stale, disagree, or miss; failed last)" in out


def test_cli_sequential(tmp_path: Path, monkeypatch, capsys) -> None:
    import httpx

    from rpcbench import run as run_mod

    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: ok\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
        )

    real = run_mod.run_endpoints

    def wrapped(config, **kwargs):
        kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(config, **kwargs)

    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(
        [
            "run",
            "--endpoints",
            str(cfg),
            "--samples",
            "1",
            "--warmup",
            "0",
            "--sequential",
            "--seed",
            "9",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Mode      sequential" in out
    assert "seed=9" in out


