from __future__ import annotations

from pathlib import Path

from rpcbench.cli import main


def test_cli_missing_file(capsys) -> None:
    code = main(["run", "--endpoints", "/no/such/endpoints.yaml"])
    assert code == 2
    err = capsys.readouterr().err
    assert "not found" in err


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
        kwargs["retries"] = 0
        return real(config, **kwargs)

    monkeypatch.setattr(run_mod, "run_endpoints", wrapped)
    # cli imports run_endpoints at function call from rpcbench.run — patch cli too
    import rpcbench.cli as cli

    monkeypatch.setattr(cli, "run_endpoints", wrapped)
    code = main(["run", "--endpoints", str(cfg), "--retries", "0"])
    out = capsys.readouterr().out
    assert code == 0
    assert "ok" in out
    assert "bad" in out
