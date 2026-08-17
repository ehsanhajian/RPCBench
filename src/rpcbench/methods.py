"""Read-only JSON-RPC methods used for latency probes."""

from __future__ import annotations

import json
from typing import Any

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Presets: chain head, identity, and a cheap account read.
PRESETS: dict[str, tuple[str, list[Any]]] = {
    "head": ("eth_blockNumber", []),
    "chainId": ("eth_chainId", []),
    "balance": ("eth_getBalance", [ZERO_ADDRESS, "latest"]),
}

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
        _reject_writes(name)
        return name, params
    name = (method or "eth_blockNumber").strip()
    if not name:
        raise MethodError("method is required")
    _reject_writes(name)
    params = parse_params(params_json) if params_json else []
    return name, params


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
        raise MethodError(f"{method} is a write method; RPCBench probes are read-only")
