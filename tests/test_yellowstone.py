from __future__ import annotations

import csv
import io
import json
import threading
import time
from typing import Iterator

import httpx
import pytest

from rpcbench.config import ConfigError, parse_endpoints
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.markdown import format_md
from rpcbench.report import format_run, run_to_dict
from rpcbench.run import run_endpoints
from rpcbench.yellowstone import (
    SlotEvent,
    lag_histogram,
    race_slots,
    run_yellowstone_race,
    yellowstone_label,
)


def _ok(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    ident = payload.get("id", 1) if isinstance(payload, dict) else 1
    method = payload.get("method") if isinstance(payload, dict) else ""
    if method == "getSlot":
        result: object = 100
    elif method == "getHealth":
        result = "ok"
    elif method == "getVersion":
        result = {"solana-core": "1.0.0"}
    else:
        result = {"context": {"slot": 100}, "value": {}}
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": ident, "result": result},
    )


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_ok))


def _cfg(*items: dict) -> object:
    return parse_endpoints({"endpoints": list(items)})


def _sol(*, name: str = "a", grpc: str | None = "https://127.0.0.1:10000") -> dict:
    item: dict = {
        "name": name,
        "url": f"http://127.0.0.1/{name}",
        "family": "solana",
    }
    if grpc is not None:
        item["grpc"] = grpc
    return item


def _scripted_stream(
    events: list[SlotEvent],
    *,
    connect_delay: float = 0.0,
    fail: BaseException | None = None,
) -> object:
    captured: dict = {}

    def open_stream(
        url: str,
        *,
        headers=(),
        timeout: float = 10.0,
        stop_event: threading.Event | None = None,
    ) -> Iterator[SlotEvent]:
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        if connect_delay:
            time.sleep(connect_delay)
        if fail is not None:
            raise fail
        for event in events:
            if stop_event is not None and stop_event.is_set():
                break
            yield event

    open_stream.captured = captured  # type: ignore[attr-defined]
    return open_stream


def _race_open(scripts: dict[str, list[SlotEvent]], *, fail: dict[str, BaseException] | None = None):
    """Map grpc URL host path suffix → events. URLs end with /name."""

    def open_stream(
        url: str,
        *,
        headers=(),
        timeout: float = 10.0,
        stop_event: threading.Event | None = None,
    ) -> Iterator[SlotEvent]:
        key = url.rstrip("/").rsplit("/", 1)[-1]
        if fail and key in fail:
            raise fail[key]
        for event in scripts.get(key, []):
            if stop_event is not None and stop_event.is_set():
                break
            yield event
            time.sleep(0.001)

    return open_stream


def test_race_slots_first_seen_and_lag() -> None:
    t0 = 100.0
    events = [
        ("fast", SlotEvent(slot=1, status="processed", t_mono=t0)),
        ("slow", SlotEvent(slot=1, status="processed", t_mono=t0 + 0.05)),
        ("fast", SlotEvent(slot=2, status="processed", t_mono=t0 + 0.1)),
        ("slow", SlotEvent(slot=2, status="processed", t_mono=t0 + 0.12)),
    ]
    wins, lags, n = race_slots(events)
    assert n == 2
    assert wins == {"fast": 2}
    assert lags["slow"] == pytest.approx([50.0, 20.0])


def test_lag_histogram_buckets() -> None:
    hist = dict(lag_histogram([10.0, 75.0, 200.0, 500.0, 2000.0]))
    assert hist["<50ms"] == 1
    assert hist["<100ms"] == 1
    assert hist["<250ms"] == 1
    assert hist["<1s"] == 1
    assert hist[">=1s"] == 1


def test_yellowstone_two_endpoints_first_seen_table() -> None:
    t0 = time.monotonic()
    opener = _race_open(
        {
            "a": [
                SlotEvent(1, "processed", t0),
                SlotEvent(2, "processed", t0 + 0.02),
            ],
            "b": [
                SlotEvent(1, "processed", t0 + 0.01),
                SlotEvent(2, "processed", t0 + 0.015),
            ],
        }
    )
    config = _cfg(
        _sol(name="a", grpc="https://ys.example/a"),
        _sol(name="b", grpc="https://ys.example/b"),
    )
    result = run_endpoints(
        config,
        method="getSlot",
        samples=1,
        warmup=0,
        timeout=2.0,
        budget=20,
        client=_client(),
        family="solana",
        yellowstone=0.05,
        open_yellowstone=opener,
    )
    assert result.yellowstone == pytest.approx(0.05)
    assert result.yellowstone_race is not None
    assert result.yellowstone_race.n_slots == 2
    by_name = {hit.name: hit for hit in result.yellowstone_race.endpoints}
    assert by_name["a"].wins == 1
    assert by_name["b"].wins == 1
    assert by_name["a"].ok and by_name["b"].ok
    assert yellowstone_label(by_name["a"]) == "ok"

    compact = format_run(result, color=False)
    assert "yellowstone=0.05" in compact
    assert "Yellowstone" in compact
    assert "wins" in compact.lower() or "Wins" in compact or "wins" in compact

    data = run_to_dict(result)
    assert data["yellowstone"] == pytest.approx(0.05)
    assert data["yellowstone_race"]["n_slots"] == 2
    assert data["ranking"][0]["yellowstone"]["wins"] >= 0

    md = format_md(result)
    assert "## Yellowstone" in md
    html = format_html(result)
    assert "Yellowstone" in html
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["yellowstone_status"] == "ok"
    assert int(rows[0]["yellowstone_n"]) >= 1


def test_yellowstone_missing_url_is_not_configured() -> None:
    config = _cfg(_sol(name="solo", grpc=None))
    result = run_endpoints(
        config,
        method="getSlot",
        samples=1,
        warmup=0,
        timeout=2.0,
        budget=20,
        client=_client(),
        family="solana",
        yellowstone=0.05,
        open_yellowstone=_scripted_stream([]),
    )
    hit = result.outcomes[0].yellowstone
    assert hit is not None
    assert hit.skip == "config"
    assert yellowstone_label(hit) == "not configured"
    data = run_to_dict(result)
    assert data["ranking"][0]["yellowstone"]["status"] == "not configured"
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["yellowstone_status"] == "not configured"


def test_yellowstone_off_omits_section() -> None:
    config = _cfg(_sol())
    result = run_endpoints(
        config,
        method="getSlot",
        samples=1,
        warmup=0,
        timeout=2.0,
        budget=20,
        client=_client(),
        family="solana",
    )
    assert result.yellowstone == 0.0
    assert result.yellowstone_race is None
    assert result.outcomes[0].yellowstone is None
    compact = format_run(result, color=False)
    assert "yellowstone=" not in compact
    data = run_to_dict(result)
    assert "yellowstone" not in data
    assert "yellowstone" not in data["ranking"][0]
    rows = list(csv.DictReader(io.StringIO(format_csv(result))))
    assert rows[0]["yellowstone_status"] == ""


def test_yellowstone_not_mixed_into_ranking() -> None:
    t0 = time.monotonic()
    opener = _race_open(
        {"a": [SlotEvent(1, "processed", t0), SlotEvent(2, "processed", t0 + 0.01)]}
    )
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return _ok(request)

    config = _cfg(_sol(name="a", grpc="https://ys.example/a"))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    plain = run_endpoints(
        config,
        method="getSlot",
        samples=1,
        warmup=0,
        timeout=2.0,
        budget=20,
        client=client,
        family="solana",
    )
    used = n["i"]
    n["i"] = 0
    measured = run_endpoints(
        config,
        method="getSlot",
        samples=1,
        warmup=0,
        timeout=2.0,
        budget=20,
        client=client,
        family="solana",
        yellowstone=0.05,
        open_yellowstone=opener,
    )
    assert n["i"] == used
    assert plain.outcomes[0].stats.n_ok == measured.outcomes[0].stats.n_ok
    assert measured.outcomes[0].yellowstone is not None
    assert plain.outcomes[0].yellowstone is None


def test_yellowstone_family_skip_for_evm() -> None:
    config = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "local",
                    "url": "http://127.0.0.1/1",
                    "grpc": "https://ys.example/local",
                }
            ]
        }
    )

    def _evm_ok(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ident = payload.get("id", 1)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": ident, "result": "0x1"}
        )

    result = run_endpoints(
        config,
        method="eth_blockNumber",
        samples=1,
        warmup=0,
        timeout=2.0,
        budget=20,
        client=httpx.Client(transport=httpx.MockTransport(_evm_ok)),
        family="evm",
        yellowstone=0.05,
        open_yellowstone=_scripted_stream([SlotEvent(1, "processed", time.monotonic())]),
    )
    hit = result.outcomes[0].yellowstone
    assert hit is not None
    assert hit.skip == "family"
    assert yellowstone_label(hit) == "skip/family"


def test_yellowstone_deps_error() -> None:
    from rpcbench.yellowstone import YellowstoneDepsError

    def bad_open(*_a, **_k):
        raise YellowstoneDepsError("missing deps")

    config = _cfg(_sol(name="a", grpc="https://ys.example/a"))
    result = run_endpoints(
        config,
        method="getSlot",
        samples=1,
        warmup=0,
        timeout=2.0,
        budget=20,
        client=_client(),
        family="solana",
        yellowstone=0.05,
        open_yellowstone=bad_open,
    )
    hit = result.outcomes[0].yellowstone
    assert hit is not None
    assert hit.skip == "deps" or hit.error_class == "deps"
    assert "deps" in yellowstone_label(hit)


def test_yellowstone_connection_failure() -> None:
    config = _cfg(_sol(name="a", grpc="https://ys.example/a"))
    result = run_endpoints(
        config,
        method="getSlot",
        samples=1,
        warmup=0,
        timeout=2.0,
        budget=20,
        client=_client(),
        family="solana",
        yellowstone=0.05,
        open_yellowstone=_scripted_stream([], fail=ConnectionError("down")),
    )
    hit = result.outcomes[0].yellowstone
    assert hit is not None
    assert hit.ok is False
    assert hit.error_class == "connection"


def test_run_yellowstone_race_direct() -> None:
    t0 = time.monotonic()
    endpoints = parse_endpoints(
        {
            "endpoints": [
                _sol(name="fast", grpc="https://ys.example/fast"),
                _sol(name="slow", grpc="https://ys.example/slow"),
            ]
        }
    ).endpoints
    race = run_yellowstone_race(
        endpoints,
        window=0.05,
        timeout=2.0,
        family="solana",
        open_stream=_race_open(
            {
                "fast": [SlotEvent(9, "processed", t0)],
                "slow": [SlotEvent(9, "processed", t0 + 0.02)],
            }
        ),
    )
    assert race.n_slots == 1
    by_name = {hit.name: hit for hit in race.endpoints}
    assert by_name["fast"].wins == 1
    assert by_name["slow"].wins == 0
    assert by_name["slow"].lag_p50_ms == pytest.approx(20.0, abs=5.0)


def test_grpc_url_config_aliases() -> None:
    cfg = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "a",
                    "url": "http://127.0.0.1/a",
                    "family": "solana",
                    "yellowstone": "https://ys.example",
                }
            ]
        }
    )
    assert cfg.endpoints[0].grpc_url == "https://ys.example"
    assert "ys.example" in (cfg.endpoints[0].display_grpc_url or "")

    with pytest.raises(ConfigError, match="conflicting"):
        parse_endpoints(
            {
                "endpoints": [
                    {
                        "name": "b",
                        "url": "http://127.0.0.1/b",
                        "grpc": "https://a.example",
                        "yellowstone": "https://b.example",
                    }
                ]
            }
        )


def test_grpc_host_port_form() -> None:
    cfg = parse_endpoints(
        {
            "endpoints": [
                {
                    "name": "a",
                    "url": "http://127.0.0.1/a",
                    "family": "solana",
                    "grpc": "yellowstone.example:10000",
                }
            ]
        }
    )
    assert cfg.endpoints[0].grpc_url == "yellowstone.example:10000"
