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
    assert "0.1.0" in out


def test_cli_missing_file(capsys) -> None:
    code = main(["run", "--endpoints", "/no/such/endpoints.yaml"])
    assert code == 2
    err = capsys.readouterr().err
    assert "not found" in err


def test_cli_defaults() -> None:
    ns = build_parser().parse_args(["run", "--endpoints", "x.yaml"])
    assert ns.samples == 10
    assert ns.warmup == 1
    assert ns.budget == 128
    assert ns.method is None
    assert ns.preset is None
    assert ns.verbose is False
    assert ns.allow_writes is False
    assert ns.max_duration == 600.0
    assert ns.concurrency == 0
    assert ns.sequential is False
    assert ns.seed == 0
    assert ns.json is False
    assert ns.output is None
    assert ns.rank_by == "p95"


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
    assert "min=" in out
    assert "p50=" in out
    assert "err=" in out
    assert "Fastest" in out
    assert "Ranking" in out
    assert "Comparison" in out
    assert "Capabilities" in out
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
    assert methods == ["eth_getBalance"]
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
    assert methods == ["eth_chainId"]


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
    assert "Comparison" in out


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
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "127.0.0.1" in out
    assert "id=" in out


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
    assert methods == ["eth_sendRawTransaction"]


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
        ["run", "--endpoints", "http://127.0.0.1:8545", "--budget", "5"]
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
    assert "RPCBench" not in out
    data = json.loads(out)
    assert data["schema"] == 1
    assert data["mode"] == "paired"
    assert data["seed"] == 0
    assert data["sequence_id"]
    assert data["pairs"]
    assert data["summary"]["fastest"] == "ok"
    assert data["providers"][0]["id"]
    assert data["ranking"][0]["name"] == "ok"


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


def test_cli_rejects_bad_rank_by(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    code = main(["run", "--endpoints", str(cfg), "--rank-by", "latency"])
    assert code == 2
    assert "rank-by" in capsys.readouterr().err


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
    assert "Ranking  (by mean; failed last)" in out


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


