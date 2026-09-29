"""Solana Yellowstone/gRPC first-seen race. Not mixed into ranking."""

from __future__ import annotations

import queue
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from rpcbench.config import ConfigError, Endpoint
from rpcbench.family import FAMILY_SOLANA, normalize_family

DEFAULT_YELLOWSTONE = 3.0
MAX_YELLOWSTONE = 10.0

# Lag buckets for the histogram (ms).
_LAG_BUCKETS = (
    (50.0, "<50ms"),
    (100.0, "<100ms"),
    (250.0, "<250ms"),
    (1000.0, "<1s"),
)

CAVEAT = (
    "Geography dominates first-seen; measure from the trading vantage. "
    "Not transaction landing, shred inclusion, or an HTTP getSlot substitute."
)

OpenStream = Callable[..., Iterator["SlotEvent"]]


@dataclass(frozen=True)
class SlotEvent:
    """One slot/status sighting from one gRPC stream."""

    slot: int
    status: str
    t_mono: float


@dataclass(frozen=True)
class YellowstoneEndpointHit:
    """Per-endpoint summary for one race window."""

    name: str
    ok: bool
    connect_ms: float | None
    n_events: int
    wins: int
    lag_p50_ms: float | None
    lag_p95_ms: float | None
    error: str | None
    error_class: str | None
    skip: str | None


@dataclass(frozen=True)
class YellowstoneRace:
    """Cross-endpoint first-seen race for one window."""

    window_s: float
    n_slots: int
    histogram: tuple[tuple[str, int], ...]
    endpoints: tuple[YellowstoneEndpointHit, ...]
    caveat: str = CAVEAT


def skipped_endpoint(name: str, reason: str) -> YellowstoneEndpointHit:
    return YellowstoneEndpointHit(
        name=name,
        ok=False,
        connect_ms=None,
        n_events=0,
        wins=0,
        lag_p50_ms=None,
        lag_p95_ms=None,
        error=None,
        error_class=reason,
        skip=reason,
    )


def yellowstone_label(hit: YellowstoneEndpointHit) -> str:
    if hit.skip == "config":
        return "not configured"
    if hit.skip:
        return f"skip/{hit.skip}"
    if hit.ok:
        return "ok"
    return hit.error_class or "fail"


def as_dict(race: YellowstoneRace) -> dict[str, Any]:
    return {
        "window_s": race.window_s,
        "n_slots": race.n_slots,
        "histogram": [{"bucket": name, "n": n} for name, n in race.histogram],
        "caveat": race.caveat,
        "endpoints": [
            {
                "name": hit.name,
                "ok": hit.ok,
                "connect_ms": hit.connect_ms,
                "n_events": hit.n_events,
                "wins": hit.wins,
                "lag_p50_ms": hit.lag_p50_ms,
                "lag_p95_ms": hit.lag_p95_ms,
                "error": hit.error,
                "error_class": hit.error_class,
                "skip": hit.skip,
                "status": yellowstone_label(hit),
            }
            for hit in race.endpoints
        ],
    }


def lag_histogram(lags_ms: list[float]) -> tuple[tuple[str, int], ...]:
    counts = {label: 0 for _edge, label in _LAG_BUCKETS}
    counts[">=1s"] = 0
    for lag in lags_ms:
        placed = False
        for edge, label in _LAG_BUCKETS:
            if lag < edge:
                counts[label] += 1
                placed = True
                break
        if not placed:
            counts[">=1s"] += 1
    order = [label for _edge, label in _LAG_BUCKETS] + [">=1s"]
    return tuple((label, counts[label]) for label in order)


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (p / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def race_slots(
    events: list[tuple[str, SlotEvent]],
) -> tuple[dict[str, int], dict[str, list[float]], int]:
    """Compute first-seen wins and per-endpoint lag samples from sightings."""
    by_slot: dict[int, list[tuple[str, float]]] = {}
    for name, event in events:
        by_slot.setdefault(event.slot, []).append((name, event.t_mono))
    wins: Counter[str] = Counter()
    lags: dict[str, list[float]] = {}
    for sightings in by_slot.values():
        ordered = sorted(sightings, key=lambda row: row[1])
        winner, t0 = ordered[0]
        wins[winner] += 1
        for name, t in ordered[1:]:
            lags.setdefault(name, []).append((t - t0) * 1000.0)
    return dict(wins), lags, len(by_slot)


def run_yellowstone_race(
    endpoints: tuple[Endpoint, ...] | list[Endpoint],
    *,
    window: float,
    timeout: float,
    family: str,
    deadline: float | None = None,
    open_stream: OpenStream | None = None,
) -> YellowstoneRace:
    """Subscribe N Solana gRPC endpoints and race which sees each slot first."""
    try:
        key = normalize_family(family)
    except ConfigError:
        key = family
    if key != FAMILY_SOLANA:
        hits = tuple(
            skipped_endpoint(endpoint.name, "family") for endpoint in endpoints
        )
        return YellowstoneRace(
            window_s=window, n_slots=0, histogram=lag_histogram([]), endpoints=hits
        )

    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            hits = tuple(
                skipped_endpoint(endpoint.name, "duration") for endpoint in endpoints
            )
            return YellowstoneRace(
                window_s=window,
                n_slots=0,
                histogram=lag_histogram([]),
                endpoints=hits,
            )
        window = min(window, remaining)
        timeout = min(timeout, remaining)

    prepared: list[tuple[Endpoint, str | None]] = []
    skips: list[YellowstoneEndpointHit] = []
    for endpoint in endpoints:
        if not endpoint.grpc_url:
            skips.append(skipped_endpoint(endpoint.name, "config"))
        else:
            prepared.append((endpoint, endpoint.grpc_url))

    if not prepared:
        return YellowstoneRace(
            window_s=window,
            n_slots=0,
            histogram=lag_histogram([]),
            endpoints=tuple(skips),
        )

    opener = open_stream or default_open_stream
    stop = threading.Event()
    inbox: queue.Queue[tuple[str, SlotEvent] | tuple[str, str, str]] = queue.Queue()
    connect_ms: dict[str, float | None] = {ep.name: None for ep, _ in prepared}
    errors: dict[str, tuple[str, str]] = {}
    counts: Counter[str] = Counter()

    def worker(endpoint: Endpoint, url: str) -> None:
        t0 = time.monotonic()
        try:
            stream = opener(
                url,
                headers=endpoint.headers,
                timeout=timeout,
                stop_event=stop,
            )
            connect_ms[endpoint.name] = (time.monotonic() - t0) * 1000.0
            for event in stream:
                if stop.is_set():
                    break
                counts[endpoint.name] += 1
                inbox.put((endpoint.name, event))
        except YellowstoneDepsError as exc:
            connect_ms[endpoint.name] = (time.monotonic() - t0) * 1000.0
            errors[endpoint.name] = (str(exc), "deps")
        except Exception as exc:
            connect_ms[endpoint.name] = (time.monotonic() - t0) * 1000.0
            errors[endpoint.name] = (str(exc) or exc.__class__.__name__, "connection")

    threads = [
        threading.Thread(
            target=worker, args=(endpoint, url), name=f"ys-{endpoint.name}", daemon=True
        )
        for endpoint, url in prepared
    ]
    for thread in threads:
        thread.start()

    deadline_mono = time.monotonic() + window
    sightings: list[tuple[str, SlotEvent]] = []
    while time.monotonic() < deadline_mono:
        remaining = deadline_mono - time.monotonic()
        if remaining <= 0:
            break
        try:
            item = inbox.get(timeout=min(0.05, remaining))
        except queue.Empty:
            continue
        if isinstance(item[1], SlotEvent):
            sightings.append((item[0], item[1]))
        else:
            # name, error, class
            errors[item[0]] = (item[1], item[2])  # type: ignore[index]

    stop.set()
    for thread in threads:
        thread.join(timeout=1.0)
    while True:
        try:
            item = inbox.get_nowait()
        except queue.Empty:
            break
        if isinstance(item[1], SlotEvent):
            sightings.append((item[0], item[1]))

    wins, lags, n_slots = race_slots(sightings)
    all_lags = [lag for samples in lags.values() for lag in samples]
    hits: list[YellowstoneEndpointHit] = list(skips)
    for endpoint, _url in prepared:
        name = endpoint.name
        if name in errors and counts[name] == 0:
            err, klass = errors[name]
            hits.append(
                YellowstoneEndpointHit(
                    name=name,
                    ok=False,
                    connect_ms=connect_ms.get(name),
                    n_events=0,
                    wins=0,
                    lag_p50_ms=None,
                    lag_p95_ms=None,
                    error=err,
                    error_class=klass,
                    skip=klass if klass in {"deps", "family", "config", "duration"} else None,
                )
            )
            continue
        samples = lags.get(name, [])
        hits.append(
            YellowstoneEndpointHit(
                name=name,
                ok=counts[name] > 0,
                connect_ms=connect_ms.get(name),
                n_events=counts[name],
                wins=wins.get(name, 0),
                lag_p50_ms=_percentile(samples, 50),
                lag_p95_ms=_percentile(samples, 95),
                error=errors.get(name, (None, None))[0],
                error_class=errors.get(name, (None, None))[1],
                skip=None,
            )
        )
    # Preserve endpoint order from the config.
    by_name = {hit.name: hit for hit in hits}
    ordered = tuple(
        by_name[endpoint.name]
        for endpoint in endpoints
        if endpoint.name in by_name
    )
    return YellowstoneRace(
        window_s=window,
        n_slots=n_slots,
        histogram=lag_histogram(all_lags),
        endpoints=ordered,
    )


class YellowstoneDepsError(RuntimeError):
    """Raised when the optional Yellowstone gRPC stack is not installed."""


def default_open_stream(
    url: str,
    *,
    headers: tuple[tuple[str, str], ...] = (),
    timeout: float = 10.0,
    stop_event: threading.Event | None = None,
) -> Iterator[SlotEvent]:
    """Open a Yellowstone slots stream. Requires ``rpcbench[yellowstone]``."""
    try:
        import grpc  # noqa: F401
        from google.protobuf import descriptor_pool  # noqa: F401
    except ImportError as exc:
        raise YellowstoneDepsError(
            "Yellowstone gRPC needs optional deps: pip install 'rpcbench[yellowstone]'"
        ) from exc
    from rpcbench.geyser_client import subscribe_slots

    return subscribe_slots(
        url, headers=headers, timeout=timeout, stop_event=stop_event
    )
