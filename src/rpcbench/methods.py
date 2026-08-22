"""Read-only JSON-RPC methods used for latency probes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Presets: chain head, identity, and a cheap account read.
PRESETS: dict[str, tuple[str, list[Any]]] = {
    "head": ("eth_blockNumber", []),
    "chainId": ("eth_chainId", []),
    "balance": ("eth_getBalance", [ZERO_ADDRESS, "latest"]),
}


@dataclass(frozen=True)
class CallSpec:
    """One fixed, documented RPC call in a workload."""

    name: str
    method: str
    params: tuple[Any, ...]


# Default mix: head, identity, block fetch, state, call, bounded logs.
# Payloads are chain-agnostic (zero address, latest-only logs).
MIX_PROFILE: tuple[CallSpec, ...] = (
    CallSpec("head", "eth_blockNumber", ()),
    CallSpec("chainId", "eth_chainId", ()),
    CallSpec("block", "eth_getBlockByNumber", ("latest", False)),
    CallSpec("balance", "eth_getBalance", (ZERO_ADDRESS, "latest")),
    CallSpec(
        "call",
        "eth_call",
        ({"to": ZERO_ADDRESS, "data": "0x"}, "latest"),
    ),
    CallSpec(
        "logs",
        "eth_getLogs",
        (
            {
                "fromBlock": "latest",
                "toBlock": "latest",
                "address": ZERO_ADDRESS,
            },
        ),
    ),
)

_WRITE_PREFIXES = (
    "eth_send",
    "eth_sign",
    "personal_",
    "miner_",
    "admin_",
    "wallet_",
)


class MethodError(ValueError):
    pass


def resolve_method(
    *,
    method: str | None,
    preset: str | None,
    params_json: str | None,
    allow_writes: bool = False,
) -> tuple[str, list[Any]]:
    if preset and method:
        raise MethodError("use either --preset or --method, not both")
    if preset:
        key = preset.strip().lower()
        matched = next((name for name in PRESETS if name.lower() == key), None)
        if matched is None:
            known = ", ".join(sorted(PRESETS))
            raise MethodError(f"unknown preset {preset!r} (try {known})")
        name, params = PRESETS[matched]
        if params_json:
            params = parse_params(params_json)
        if not allow_writes:
            _reject_writes(name)
        return name, params
    name = (method or "eth_blockNumber").strip()
    if not name:
        raise MethodError("method is required")
    if not allow_writes:
        _reject_writes(name)
    params = parse_params(params_json) if params_json else []
    return name, params


def resolve_workload(
    *,
    profile: str | None,
    method: str | None,
    preset: str | None,
    params_json: str | None,
    allow_writes: bool = False,
) -> tuple[str, tuple[CallSpec, ...]]:
    """Return (label, steps). Label is ``mix`` or the single JSON-RPC method."""
    if profile:
        key = profile.strip().lower()
        if key != "mix":
            raise MethodError(f"unknown --profile {profile!r} (try mix)")
        if method or preset:
            raise MethodError("use --profile without --method or --preset")
        if params_json:
            raise MethodError("mix payloads are fixed; do not pass --params")
        for spec in MIX_PROFILE:
            if not allow_writes:
                _reject_writes(spec.method)
        return "mix", MIX_PROFILE
    name, params = resolve_method(
        method=method,
        preset=preset,
        params_json=params_json,
        allow_writes=allow_writes,
    )
    step = "call"
    if preset:
        matched = next(
            (n for n in PRESETS if n.lower() == preset.strip().lower()), None
        )
        if matched:
            step = matched
    elif name == "eth_blockNumber" and not params:
        step = "head"
    return name, (CallSpec(step, name, tuple(params)),)


def parse_params(raw: str) -> list[Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MethodError(f"invalid --params JSON: {exc}") from exc
    if not isinstance(data, list):
        raise MethodError("--params must be a JSON array")
    return data


def _reject_writes(method: str) -> None:
    lower = method.lower()
    if any(lower.startswith(p) for p in _WRITE_PREFIXES):
        raise MethodError(
            f"{method} is a write method; pass --allow-writes to run it anyway"
        )
