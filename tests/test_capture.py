from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import httpx
import pytest

from rpcbench.capture import (
    CapturedCall,
    CaptureError,
    body_diff_text,
    dump_jsonl,
    format_replay,
    format_replay_csv,
    format_replay_html,
    format_replay_md,
    load_jsonl,
    record_calls,
    reject_write_calls,
    replay_calls,
    replay_to_dict,
)
from rpcbench.config import parse_endpoints
from rpcbench.methods import CallSpec, MethodError
from rpcbench.cli import build_parser, main


def _cfg() -> object:
    return parse_endpoints(
        {
            "endpoints": [
                {"name": "a", "url": "http://127.0.0.1:1"},
                {"name": "b", "url": "http://127.0.0.1:2"},
            ]
        }
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _result(request: httpx.Request, value: object) -> httpx.Response:
    payload = json.loads(request.content)
    ident = payload.get("id", 1) if isinstance(payload, dict) else 1
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": ident, "result": value},
    )


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    calls = (
        CapturedCall("eth_blockNumber", ()),
        CapturedCall("eth_getBalance", ("0x0", "latest")),
    )
    path = tmp_path / "cap.jsonl"
    path.write_text(dump_jsonl(calls), encoding="utf-8")
    loaded = load_jsonl(path)
    assert loaded == calls
    rpc = tmp_path / "rpc.jsonl"
    rpc.write_text(
        '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}\n',
        encoding="utf-8",
    )
    assert load_jsonl(rpc)[0].method == "eth_chainId"


def test_empty_capture_is_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("\n# comment\n", encoding="utf-8")
    with pytest.raises(CaptureError, match="empty"):
        load_jsonl(path)


def test_write_methods_blocked_by_default() -> None:
    calls = (CapturedCall("eth_sendRawTransaction", ("0xab",)),)
    with pytest.raises(MethodError, match="allow-writes"):
        reject_write_calls(calls, allow_writes=False)
    reject_write_calls(calls, allow_writes=True)


def test_replay_lockstep_counts_matching_bodies() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _result(request, "0x10")

    calls = (
        CapturedCall("eth_blockNumber", ()),
        CapturedCall("eth_chainId", ()),
    )
    result = replay_calls(
        _cfg(),
        calls,
        source="cap.jsonl",
        budget=32,
        client=_client(handler),
    )
    assert result.n_match == 2
    assert result.n_mismatch == 0
    assert result.n_body_mismatch == 0
    assert result.steps[0].match
    assert result.steps[0].hits[0].body_hash == result.steps[0].hits[1].body_hash
    compact = format_replay(result)
    assert "lockstep" in compact
    assert "Match     2/2" in compact
    assert "finding" not in compact.lower()
    data = replay_to_dict(result)
    assert data["command"] == "replay"
    assert data["match"] == 2
    assert data["steps"][0]["match"] is True
    html = format_replay_html(result)
    assert ">RPCBench replay</h1>" in html
    assert "finding" not in html.lower()
    md = format_replay_md(result)
    assert "# RPCBench replay" in md
    rows = list(csv.DictReader(io.StringIO(format_replay_csv(result))))
    assert [row["name"] for row in rows] == ["a", "b"]
    assert rows[0]["match"] == "2"


def test_replay_mismatched_bodies_are_counted_and_diffed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "127.0.0.1:1" in str(request.url):
            return _result(request, "0x1")
        return _result(request, "0x2")

    result = replay_calls(
        _cfg(),
        (CapturedCall("eth_blockNumber", ()),),
        budget=16,
        client=_client(handler),
    )
    assert result.n_mismatch == 1
    assert result.n_body_mismatch == 1
    assert not result.steps[0].match
    assert result.steps[0].note == "bodies"
    compact = format_replay(result, verbose=True)
    assert "Mismatch  1/1" in compact
    assert "bodies=1" in compact
    assert "Diffs" in compact
    diff = body_diff_text(result.steps[0])
    assert "0x1" in diff
    assert "0x2" in diff
    assert "finding" not in compact.lower()
    data = replay_to_dict(result)
    assert data["body_mismatch"] == 1
    assert data["steps"][0]["body_mismatch"] is True


def test_replay_status_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "127.0.0.1:2" in str(request.url):
            return httpx.Response(500, text="nope")
        return _result(request, "0x1")

    result = replay_calls(
        _cfg(),
        (CapturedCall("eth_blockNumber", ()),),
        budget=16,
        client=_client(handler),
    )
    assert result.n_status_mismatch == 1
    assert result.steps[0].status_mismatch
    compact = format_replay(result)
    assert "status" in compact.split("Calls", 1)[1]


def test_record_writes_mix_jsonl(tmp_path: Path) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    out = tmp_path / "cap.jsonl"
    code = main(
        [
            "record",
            "--endpoints",
            str(cfg),
            "--method",
            "eth_blockNumber",
            "--samples",
            "2",
            "-o",
            str(out),
        ]
    )
    assert code == 0
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"method": "eth_blockNumber", "params": []}


def test_cli_replay_blocks_writes(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n  - name: local\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    cap = tmp_path / "cap.jsonl"
    cap.write_text(
        '{"method":"eth_sendRawTransaction","params":["0xab"]}\n',
        encoding="utf-8",
    )
    code = main(["replay", "--endpoints", str(cfg), "--from", str(cap)])
    assert code == 2
    assert "--allow-writes" in capsys.readouterr().err


def test_cli_record_blocks_writes(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "record",
            "--method",
            "eth_sendTransaction",
            "-o",
            str(tmp_path / "x.jsonl"),
        ]
    )
    assert code == 2
    assert "--allow-writes" in capsys.readouterr().err


def test_cli_replay_and_record_flags() -> None:
    rec = build_parser().parse_args(["record", "--workload"])
    assert rec.command == "record"
    assert rec.workload == "general"
    assert rec.samples == 1
    assert rec.allow_writes is False
    ns = build_parser().parse_args(
        ["replay", "--endpoints", "x.yaml", "--from", "cap.jsonl", "--verbose"]
    )
    assert ns.command == "replay"
    assert ns.capture == "cap.jsonl"
    assert ns.verbose is True


def test_record_calls_from_workload() -> None:
    steps = (
        CallSpec("head", "eth_blockNumber", (), 2),
        CallSpec("id", "eth_chainId", ()),
    )
    calls = record_calls(steps, samples=1)
    assert [c.method for c in calls] == [
        "eth_blockNumber",
        "eth_blockNumber",
        "eth_chainId",
    ]


def test_cli_replay_lockstep(tmp_path: Path, capsys, monkeypatch) -> None:
    cfg = tmp_path / "e.yaml"
    cfg.write_text(
        "endpoints:\n"
        "  - name: a\n    url: http://127.0.0.1:1\n"
        "  - name: b\n    url: http://127.0.0.1:2\n",
        encoding="utf-8",
    )
    cap = tmp_path / "cap.jsonl"
    cap.write_text('{"method":"eth_blockNumber","params":[]}\n', encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return _result(request, "0xaa")

    monkeypatch.setattr(
        "rpcbench.rpc.make_client",
        lambda **kwargs: _client(handler),
    )
    code = main(
        [
            "replay",
            "--endpoints",
            str(cfg),
            "--from",
            str(cap),
            "--max-requests",
            "16",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "lockstep" in out
    assert "Match     1/1" in out
    assert "finding" not in out.lower()
