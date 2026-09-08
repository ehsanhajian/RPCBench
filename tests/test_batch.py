from __future__ import annotations

import csv
import io
import json
import time

import httpx

from rpcbench.config import parse_endpoints
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.report import format_run, run_to_dict
from rpcbench.rpc import RequestBudget, probe_batch
from rpcbench.run import run_endpoints


def _respond(
    request: httpx.Request,
    *,
    delay: float = 0.0,
    mode: str = "ok",
) -> httpx.Response:
    if delay:
        time.sleep(delay)
    payload = json.loads(request.content)
    if not isinstance(payload, list):
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload.get("id", 1), "result": "0x1"},
        )
    if mode == "unsupported":
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Batch not supported"},
            },
        )
    rows = []
    for i, item in enumerate(payload):
        ident = item.get("id")
        if mode == "partial" and i > 0:
            rows.append(
                {
                    "jsonrpc": "2.0",
                    "id": ident,
                    "error": {"code": -32000, "message": "item failed"},
                }
            )
        else:
            rows.append({"jsonrpc": "2.0", "id": ident, "result": "0x1"})
    return httpx.Response(200, json=rows)


def _client(mode: str = "ok", delay: float = 0.0) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return _respond(request, delay=delay, mode=mode)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_probe_batch_parses_array_ok() -> None:
    hit = probe_batch(
        "http://127.0.0.1:1",
        "eth_blockNumber",
        size=3,
        client=_client(),
    )
    assert hit.supported is True
    assert hit.partial is False
    assert hit.ok is True
    assert hit.n_ok == 3
    assert hit.n_fail == 0
    assert hit.error_class is None


def test_probe_batch_single_object_is_unsupported() -> None:
    hit = probe_batch(
        "http://127.0.0.1:1",
        "eth_blockNumber",
        size=3,
        client=_client(mode="unsupported"),
    )
    assert hit.supported is False
    assert hit.ok is False
    assert hit.error_class == "batch_unsupported"
    assert "Batch not supported" in (hit.error or "")


def test_probe_batch_partial_item_errors() -> None:
    hit = probe_batch(
        "http://127.0.0.1:1",
        "eth_blockNumber",
        size=3,
        client=_client(mode="partial"),
    )
    assert hit.supported is True
    assert hit.partial is True
    assert hit.ok is False
    assert hit.n_ok == 1
    assert hit.n_fail == 2
    assert hit.error_class == "jsonrpc"


def test_probe_batch_malformed_is_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    hit = probe_batch(
        "http://127.0.0.1:1",
        "eth_blockNumber",
        size=3,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert hit.supported is False
    assert hit.error_class == "malformed"


def test_probe_batch_consumes_one_budget_unit() -> None:
    purse = RequestBudget(4)
    probe_batch(
        "http://127.0.0.1:1",
        "eth_blockNumber",
        size=3,
        budget=purse,
        client=_client(),
    )
    assert purse.used == 1
    assert purse.remaining == 3


def test_batch_vs_serial_metrics_when_enabled() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=32,
        batch=3,
        client=_client(delay=0.02),
    )
    assert result.batch == 3
    summary = result.outcomes[0].batch
    assert summary is not None
    assert summary.supported is True
    assert summary.partial is False
    assert summary.size == 3
    assert summary.method == "eth_blockNumber"
    assert summary.batch_ms is not None
    assert summary.serial_ms is not None
    assert summary.serial_ms > summary.batch_ms
    assert summary.ratio is not None and summary.ratio > 1
    assert summary.n_ok == 3
    compact = format_run(result, color=False)
    assert "batch=3" in compact
    assert "Batch  (3 calls in one POST vs the same 3 sent one-by-one" in compact
    assert "yes" in compact.split("Batch", 1)[1]
    full = format_run(result, verbose=True, color=False)
    assert full.split("Batch", 1)[1].split("Providers", 1)[0].count("yes") >= 1
    assert "finding" not in full.lower()
    assert "severity" not in full.lower()
    data = run_to_dict(result)
    assert data["batch"] == 3
    blob = data["providers"][0]["batch"]
    assert blob["supported"] is True
    assert blob["n_ok"] == 3
    html = format_html(result)
    assert ">Batch</h2>" in html
    assert "not mixed into ranking" in html
    csv_text = format_csv(result)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert rows[0]["batch_supported"] == "true"
    assert float(rows[0]["batch_ms"]) > 0
    assert float(rows[0]["serial_ms"]) > float(rows[0]["batch_ms"])


def test_batch_adds_one_plus_n_requests() -> None:
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return _respond(request)

    cfg = parse_endpoints(
        {"endpoints": [{"name": "a", "url": "http://127.0.0.1:1"}]}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    plain = run_endpoints(cfg, samples=1, warmup=0, budget=32, client=client)
    used_plain = n["i"]
    n["i"] = 0
    batched = run_endpoints(
        cfg, samples=1, warmup=0, budget=32, batch=3, client=client
    )
    assert n["i"] - used_plain == 4
    assert plain.outcomes[0].stats.n_ok == batched.outcomes[0].stats.n_ok
    assert batched.outcomes[0].batch is not None
    assert plain.outcomes[0].batch is None


def test_unsupported_batch_is_capability_not_crash() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=32,
        batch=3,
        client=_client(mode="unsupported"),
    )
    summary = result.outcomes[0].batch
    assert summary is not None
    assert summary.supported is False
    assert summary.error_class == "batch_unsupported"
    full = format_run(result, verbose=True, color=False)
    assert "batch_unsupported" in full
    cap = full.split("Capabilities", 1)[1]
    assert "0/1 supported" in cap
    assert "finding" not in full.lower()
    html = format_html(result)
    assert "batch_unsupported" in html
    assert "finding" not in html.lower()


def test_partial_batch_is_reported() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg,
        samples=1,
        warmup=0,
        budget=32,
        batch=3,
        client=_client(mode="partial"),
    )
    summary = result.outcomes[0].batch
    assert summary is not None
    assert summary.supported is True
    assert summary.partial is True
    assert summary.n_ok == 1
    assert summary.n_fail == 2
    full = format_run(result, verbose=True, color=False)
    batch = full.split("Batch", 1)[1].split("Providers", 1)[0]
    assert "partial" in batch
    assert "1/3" in batch


def test_batch_off_omits_section() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=32, client=_client()
    )
    assert result.batch == 0
    assert result.outcomes[0].batch is None
    full = format_run(result, verbose=True, color=False)
    assert "Batch  (" not in full
    assert "batch=" not in full
    html = format_html(result)
    assert ">Batch</h2>" not in html
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["batch_supported"] == ""
    assert rows[0]["batch_ms"] == ""
    assert rows[0]["serial_ms"] == ""


def test_batch_table_is_in_compact_cli() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=32, batch=3, client=_client()
    )
    compact = format_run(result, verbose=False, color=False)
    assert "Batch  (" in compact
    table = compact.split("Batch", 1)[1]
    assert "support" in table
    assert "yes" in table
    assert "Providers" not in compact


def test_batch_budget_miss_is_skip_not_unsupported() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://127.0.0.1:1"}]}
    )
    result = run_endpoints(
        cfg, samples=1, warmup=0, budget=6, batch=3, client=_client()
    )
    summary = result.outcomes[0].batch
    assert summary is not None
    assert summary.error_class == "budget"
    assert summary.supported is False
    compact = format_run(result, color=False)
    table = compact.split("Batch", 1)[1]
    assert "skip" in table
    assert "budget" in table
    html = format_html(result)
    assert ">skip</td>" in html or ">skip<" in html
    assert "batch_unsupported" not in html
