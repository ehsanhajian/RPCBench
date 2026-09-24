"""Timed WebSocket subscribe extra-read. Not mixed into ranking."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from rpcbench.config import ConfigError, Endpoint
from rpcbench.family import FAMILY_EVM, benchmark_family
from rpcbench.freshness import parse_block_height

DEFAULT_WEBSOCKET = 3.0
MAX_WEBSOCKET = 10.0
_SUBSCRIBE_ID = 1

OpenWS = Callable[..., Any]


@dataclass(frozen=True)
class WebsocketHit:
    """One connect + subscribe window. Skip means no WS traffic."""

    window_s: float
    ok: bool
    connect_ms: float | None
    subscribe_ms: float | None
    first_event_ms: float | None
    n_events: int
    missed: int
    disconnects: int
    error: str | None
    error_class: str | None
    skip: str | None


def missed_heads(numbers: list[int]) -> int:
    """Count missing block heights between the min and max observed heads."""
    if len(numbers) < 2:
        return 0
    ordered = sorted(set(numbers))
    gaps = 0
    for prev, nxt in zip(ordered, ordered[1:]):
        hole = nxt - prev - 1
        if hole > 0:
            gaps += hole
    return gaps


def skipped_websocket(reason: str, *, window_s: float) -> WebsocketHit:
    return WebsocketHit(
        window_s=window_s,
        ok=False,
        connect_ms=None,
        subscribe_ms=None,
        first_event_ms=None,
        n_events=0,
        missed=0,
        disconnects=0,
        error=None,
        error_class=reason,
        skip=reason,
    )


def websocket_label(hit: WebsocketHit) -> str:
    if hit.skip == "config":
        return "not configured"
    if hit.skip:
        return f"skip/{hit.skip}"
    if hit.ok:
        return "ok"
    return hit.error_class or "fail"


def as_dict(hit: WebsocketHit) -> dict[str, Any]:
    return {
        "window_s": hit.window_s,
        "ok": hit.ok,
        "connect_ms": hit.connect_ms,
        "subscribe_ms": hit.subscribe_ms,
        "first_event_ms": hit.first_event_ms,
        "n_events": hit.n_events,
        "missed": hit.missed,
        "disconnects": hit.disconnects,
        "error": hit.error,
        "error_class": hit.error_class,
        "skip": hit.skip,
        "status": websocket_label(hit),
    }


def default_open_ws(
    url: str,
    *,
    headers: tuple[tuple[str, str], ...] = (),
    timeout: float = 10.0,
):
    from websockets.sync.client import connect

    extra = dict(headers) if headers else None
    return connect(
        url,
        additional_headers=extra,
        open_timeout=timeout,
        close_timeout=min(1.0, timeout),
        max_size=2**20,
    )


def probe_websocket(
    endpoint: Endpoint,
    *,
    window: float,
    timeout: float,
    deadline: float | None = None,
    family: str,
    open_ws: OpenWS | None = None,
) -> WebsocketHit:
    """Connect, subscribe to head notifications, listen for ``window`` seconds."""
    try:
        adapter = benchmark_family(family)
    except ConfigError:
        return skipped_websocket("family", window_s=window)
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return skipped_websocket("duration", window_s=window)
        window = min(window, remaining)
        timeout = min(timeout, remaining)
    if not endpoint.ws_url:
        return skipped_websocket("config", window_s=window)
    opener = open_ws or default_open_ws
    t0 = time.monotonic()
    try:
        ctx = opener(endpoint.ws_url, headers=endpoint.headers, timeout=timeout)
        sock = ctx.__enter__()
    except Exception as exc:
        connect_ms = (time.monotonic() - t0) * 1000.0
        return WebsocketHit(
            window_s=window,
            ok=False,
            connect_ms=connect_ms,
            subscribe_ms=None,
            first_event_ms=None,
            n_events=0,
            missed=0,
            disconnects=0,
            error=str(exc) or exc.__class__.__name__,
            error_class="connection",
            skip=None,
        )
    connect_ms = (time.monotonic() - t0) * 1000.0
    try:
        return _subscribe_and_listen(
            sock,
            window=window,
            timeout=timeout,
            connect_ms=connect_ms,
            method=adapter.ws_method,
            params=list(adapter.ws_params),
        )
    finally:
        try:
            ctx.__exit__(None, None, None)
        except Exception:
            pass


def _subscribe_and_listen(
    sock: Any,
    *,
    window: float,
    timeout: float,
    connect_ms: float,
    method: str,
    params: list[Any],
) -> WebsocketHit:
    request = {
        "jsonrpc": "2.0",
        "id": _SUBSCRIBE_ID,
        "method": method,
        "params": params,
    }
    t_sub = time.monotonic()
    try:
        sock.send(json.dumps(request))
    except Exception as exc:
        return WebsocketHit(
            window_s=window,
            ok=False,
            connect_ms=connect_ms,
            subscribe_ms=None,
            first_event_ms=None,
            n_events=0,
            missed=0,
            disconnects=1,
            error=str(exc) or exc.__class__.__name__,
            error_class="connection",
            skip=None,
        )
    ack, early, disconnects, disc_err = _recv_until(
        sock,
        timeout=timeout,
        want=_is_subscribe_ack,
    )
    subscribe_ms = (time.monotonic() - t_sub) * 1000.0
    if disconnects:
        return WebsocketHit(
            window_s=window,
            ok=False,
            connect_ms=connect_ms,
            subscribe_ms=None,
            first_event_ms=None,
            n_events=0,
            missed=0,
            disconnects=disconnects,
            error=disc_err,
            error_class="connection",
            skip=None,
        )
    if ack is None:
        return WebsocketHit(
            window_s=window,
            ok=False,
            connect_ms=connect_ms,
            subscribe_ms=None,
            first_event_ms=None,
            n_events=0,
            missed=0,
            disconnects=0,
            error="subscribe timed out",
            error_class="timeout",
            skip="timeout",
        )
    if ack.get("error") is not None:
        return WebsocketHit(
            window_s=window,
            ok=False,
            connect_ms=connect_ms,
            subscribe_ms=subscribe_ms,
            first_event_ms=None,
            n_events=0,
            missed=0,
            disconnects=0,
            error=_error_text(ack.get("error")),
            error_class="unsupported",
            skip="unsupported",
        )
    if ack.get("result") in (None, ""):
        return WebsocketHit(
            window_s=window,
            ok=False,
            connect_ms=connect_ms,
            subscribe_ms=subscribe_ms,
            first_event_ms=None,
            n_events=0,
            missed=0,
            disconnects=0,
            error="subscribe returned no id",
            error_class="malformed",
            skip=None,
        )
    numbers: list[int] = []
    n_events = 0
    first_event_ms = None
    t_ack = time.monotonic()
    for msg in early:
        n_events, first_event_ms = _note_event(
            msg, numbers, n_events, first_event_ms, t_ack
        )
    listen_until = time.monotonic() + window
    while time.monotonic() < listen_until:
        remaining = listen_until - time.monotonic()
        if remaining <= 0:
            break
        msg, more, more_disc, more_err = _recv_until(
            sock,
            timeout=min(remaining, 0.1),
            want=_is_heads,
        )
        disconnects += more_disc
        if more_disc:
            return _ok_or_drop(
                window=window,
                connect_ms=connect_ms,
                subscribe_ms=subscribe_ms,
                first_event_ms=first_event_ms,
                numbers=numbers,
                n_events=n_events,
                disconnects=disconnects,
                error=more_err,
            )
        for item in more:
            n_events, first_event_ms = _note_event(
                item, numbers, n_events, first_event_ms, t_ack
            )
        if msg is not None:
            n_events, first_event_ms = _note_event(
                msg, numbers, n_events, first_event_ms, t_ack
            )
    return WebsocketHit(
        window_s=window,
        ok=True,
        connect_ms=connect_ms,
        subscribe_ms=subscribe_ms,
        first_event_ms=first_event_ms,
        n_events=n_events,
        missed=missed_heads(numbers),
        disconnects=0,
        error=None,
        error_class=None,
        skip=None,
    )


def _ok_or_drop(
    *,
    window: float,
    connect_ms: float,
    subscribe_ms: float,
    first_event_ms: float | None,
    numbers: list[int],
    n_events: int,
    disconnects: int,
    error: str | None,
) -> WebsocketHit:
    return WebsocketHit(
        window_s=window,
        ok=True,
        connect_ms=connect_ms,
        subscribe_ms=subscribe_ms,
        first_event_ms=first_event_ms,
        n_events=n_events,
        missed=missed_heads(numbers),
        disconnects=disconnects,
        error=error,
        error_class="connection" if n_events == 0 else None,
        skip=None,
    )


def _note_event(
    msg: dict[str, Any],
    numbers: list[int],
    n_events: int,
    first_event_ms: float | None,
    t_ack: float,
) -> tuple[int, float | None]:
    height = _head_number(msg)
    if height is None and not _is_heads(msg):
        return n_events, first_event_ms
    n_events += 1
    if first_event_ms is None:
        first_event_ms = (time.monotonic() - t_ack) * 1000.0
    if height is not None:
        numbers.append(height)
    return n_events, first_event_ms


def _recv_until(
    sock: Any,
    *,
    timeout: float,
    want,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int, str | None]:
    extra: list[dict[str, Any]] = []
    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            raw = sock.recv(timeout=remaining)
        except TimeoutError:
            break
        except Exception as exc:
            return None, extra, 1, str(exc) or exc.__class__.__name__
        msg = _decode(raw)
        if msg is None:
            continue
        if want(msg):
            return msg, extra, 0, None
        if _is_heads(msg):
            extra.append(msg)
    return None, extra, 0, None


def _decode(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return None
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return msg if isinstance(msg, dict) else None


def _is_subscribe_ack(msg: dict[str, Any]) -> bool:
    ident = msg.get("id")
    if ident != _SUBSCRIBE_ID and ident != str(_SUBSCRIBE_ID):
        return False
    return "result" in msg or "error" in msg


def _is_heads(msg: dict[str, Any]) -> bool:
    method = msg.get("method")
    if method in {"eth_subscription", "slotNotification", "logsNotification"}:
        return True
    params = msg.get("params")
    if isinstance(params, dict) and "result" in params:
        return True
    return False


def _head_number(msg: dict[str, Any]) -> int | None:
    params = msg.get("params")
    result = params.get("result") if isinstance(params, dict) else None
    if isinstance(result, dict):
        height = parse_block_height(result.get("number"))
        if height is not None:
            return height
        return parse_block_height(result.get("slot"))
    return parse_block_height(result)


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
        return json.dumps(error, sort_keys=True)
    return str(error)


# Back-compat aliases for tests that imported EVM subscribe constants.
SUBSCRIBE_METHOD = benchmark_family(FAMILY_EVM).ws_method
SUBSCRIBE_PARAMS = benchmark_family(FAMILY_EVM).ws_params
