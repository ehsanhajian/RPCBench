"""Custom YAML method mixes and seeded on-chain payloads."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from rpcbench.methods import (
    CALL_TX,
    ZERO_ADDRESS,
    CallSpec,
    MethodError,
    WorkloadPlan,
    _reject_writes,
    canonical_workload,
    is_debug_recon,
    is_debug_trace_method,
    is_trace_filter,
    is_trace_method,
    simulate_params,
    debug_trace_call_params,
    trace_block_params,
    trace_call_params,
)

# Look-back for source: recent_block. Inclusive of head.
RECENT_WINDOW = 256

SOURCES = (
    "latest_head",
    "recent_block",
    "known_contract",
    "seeded_address",
)

# Documented WETH (or wrapped native) when the chain is known. Else zero address.
KNOWN_CONTRACTS: dict[int, str] = {
    1: "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    10: "0x4200000000000000000000000000000000000006",
    8453: "0x4200000000000000000000000000000000000006",
    137: "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
}

_SHORT_NAMES = {
    "eth_blockNumber": "head",
    "eth_chainId": "chainId",
    "eth_getBlockByNumber": "block",
    "eth_getBalance": "balance",
    "eth_call": "call",
    "eth_estimateGas": "gas",
    "eth_simulateV1": "simulate",
    "eth_getLogs": "logs",
    "trace_block": "trace",
    "trace_call": "traceCall",
    "debug_traceCall": "debug",
}

_BLOCK_SOURCES = frozenset({"latest_head", "recent_block"})
_ADDRESS_SOURCES = frozenset({"known_contract", "seeded_address"})
_PARAM_METHODS = frozenset(
    {
        "eth_getBlockByNumber",
        "eth_getBalance",
        "eth_call",
        "eth_estimateGas",
        "eth_simulateV1",
        "eth_getLogs",
        "trace_block",
        "trace_call",
        "debug_traceCall",
    }
)

# Never in a YAML mix, even with --allow-writes.
# debug_traceCall is the cheap timed exception (always optional).
_PRIVILEGED_PREFIXES = (
    "engine_",
    "txpool_",
    "clique_",
    "admin_",
    "miner_",
)


@dataclass(frozen=True)
class PayloadMeta:
    """How mix params were filled. Extra chain reads are not timed samples."""

    source: str  # chain | seed | fixture
    head: int | None = None
    chain_id: int | None = None
    fallback: bool = False
    window: int = RECENT_WINDOW


def as_profile_path(raw: str | None) -> Path | None:
    """YAML/JSON mix file, or None if this is a catalog name / not a path."""
    if not raw:
        return None
    text = raw.strip()
    if not text or canonical_workload(text) is not None:
        return None
    path = Path(text)
    suffix = path.suffix.lower()
    looks_like = suffix in {".yaml", ".yml", ".json"} or any(
        mark in text for mark in ("/", "\\")
    ) or text.startswith(".")
    if path.is_file():
        return path
    if looks_like:
        return path
    return None


def load_profile(path: Path | str, *, allow_writes: bool = False) -> WorkloadPlan:
    raw_path = Path(path)
    if not raw_path.is_file():
        raise MethodError(f"profile file not found: {raw_path}")
    text = raw_path.read_text(encoding="utf-8")
    suffix = raw_path.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise MethodError(f"invalid profile file {raw_path}: {exc}") from exc
    return parse_profile(data, source=str(raw_path), allow_writes=allow_writes)


def parse_profile(
    data: object,
    *,
    source: str = "profile",
    allow_writes: bool = False,
) -> WorkloadPlan:
    if not isinstance(data, dict):
        raise MethodError(f"{source}: expected a mapping with a 'methods' list")
    methods = data.get("methods")
    if not isinstance(methods, list) or not methods:
        raise MethodError(f"{source}: 'methods' must be a non-empty list")
    raw_name = data.get("name")
    if raw_name is None:
        label = Path(source).stem if source not in {"profile", "config"} else "custom"
    elif isinstance(raw_name, str) and raw_name.strip():
        label = raw_name.strip()
    else:
        raise MethodError(f"{source}: 'name' must be a non-empty string")
    if label.lower() == "single":
        raise MethodError(f"{source}: profile name 'single' is reserved")
    notes = data.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise MethodError(f"{source}: 'notes' must be a string")
    timeout = _parse_timeout(data.get("timeout"), source=source)
    default_contract = _parse_address(data.get("contract"), field="contract", source=source)
    seen: dict[str, int] = {}
    steps: list[CallSpec] = []
    for i, item in enumerate(methods):
        spec = _parse_step(
            item,
            index=i,
            source=source,
            allow_writes=allow_writes,
            default_contract=default_contract,
        )
        count = seen.get(spec.name, 0) + 1
        seen[spec.name] = count
        if count > 1:
            spec = replace(spec, name=f"{spec.name}_{count}")
        steps.append(spec)
    return WorkloadPlan(
        label=label,
        steps=tuple(steps),
        notes=notes.strip() if isinstance(notes, str) and notes.strip() else None,
        timeout=timeout,
    )


def hint_needs(steps: tuple[CallSpec, ...]) -> tuple[bool, bool]:
    """Whether bind needs a shared head and/or chainId before timed samples."""
    need_head = any(spec.source in _BLOCK_SOURCES for spec in steps)
    need_chain = any(
        spec.source == "known_contract" and not spec.contract for spec in steps
    )
    return need_head, need_chain


def hint_request_count(steps: tuple[CallSpec, ...]) -> int:
    need_head, need_chain = hint_needs(steps)
    return int(need_head) + int(need_chain)


def has_dynamic_source(steps: tuple[CallSpec, ...]) -> bool:
    return any(spec.source for spec in steps)


def known_contract(chain_id: int | None) -> str:
    if chain_id is None:
        return ZERO_ADDRESS
    return KNOWN_CONTRACTS.get(chain_id, ZERO_ADDRESS)


def seeded_address(seed: int, index: int) -> str:
    digest = hashlib.sha256(f"rpcbench:{seed}:{index}".encode("utf-8")).digest()
    return "0x" + digest[:20].hex()


def recent_block(head: int, rng: random.Random) -> int:
    window = min(RECENT_WINDOW, head) if head > 0 else 0
    offset = rng.randint(0, window) if window else 0
    return max(0, head - offset)


def bind_workload(
    steps: tuple[CallSpec, ...],
    *,
    seed: int,
    head: int | None,
    chain_id: int | None,
) -> tuple[tuple[CallSpec, ...], bool]:
    """Fill source-tagged params. Same seed + hints ⇒ same params. Returns fallback used."""
    rng = random.Random(seed)
    used_fixture = False
    bound: list[CallSpec] = []
    for index, spec in enumerate(steps):
        if not spec.source:
            bound.append(spec)
            continue
        params, fell = _fill_params(
            spec,
            seed=seed,
            index=index,
            head=head,
            chain_id=chain_id,
            rng=rng,
        )
        used_fixture = used_fixture or fell
        bound.append(replace(spec, params=params))
    return tuple(bound), used_fixture


def payload_kind(
    steps: tuple[CallSpec, ...],
    *,
    head: int | None,
    chain_id: int | None,
    fallback: bool,
) -> str:
    need_head, need_chain = hint_needs(steps)
    missed = (need_head and head is None) or (need_chain and chain_id is None)
    if (fallback or missed) and (need_head or need_chain):
        return "fixture"
    if need_head or need_chain:
        return "chain"
    sources = {spec.source for spec in steps if spec.source}
    if sources <= {"seeded_address"} and sources:
        return "seed"
    if sources:
        return "yaml"
    return "fixture"


def _parse_timeout(raw: object, *, source: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise MethodError(f"{source}: 'timeout' must be a positive number")
    value = float(raw)
    if value <= 0:
        raise MethodError(f"{source}: 'timeout' must be a positive number")
    return value


def _parse_address(raw: object, *, field: str, source: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise MethodError(f"{source}: '{field}' must be a 0x address")
    text = raw.strip()
    if not text.startswith("0x") or len(text) != 42:
        raise MethodError(f"{source}: '{field}' must be a 0x address")
    try:
        int(text, 16)
    except ValueError as exc:
        raise MethodError(f"{source}: '{field}' must be a 0x address") from exc
    return text.lower()


def _parse_step(
    item: object,
    *,
    index: int,
    source: str,
    allow_writes: bool,
    default_contract: str | None,
) -> CallSpec:
    loc = f"{source}: methods[{index}]"
    if not isinstance(item, dict):
        raise MethodError(f"{loc} must be a mapping")
    method = item.get("method")
    if not isinstance(method, str) or not method.strip():
        raise MethodError(f"{loc}.method is required")
    method = method.strip()
    _reject_profile_method(method, allow_writes=allow_writes)
    weight = item.get("weight", 1)
    if isinstance(weight, bool) or not isinstance(weight, int) or weight < 1:
        raise MethodError(f"{loc}.weight must be an integer >= 1")
    src = item.get("source")
    if src is not None:
        if not isinstance(src, str) or src.strip() not in SOURCES:
            known = ", ".join(SOURCES)
            raise MethodError(f"{loc}.source must be one of {known}")
        src = src.strip()
        if method not in _PARAM_METHODS:
            raise MethodError(f"{loc}: source {src} is not valid for {method}")
        if src in _ADDRESS_SOURCES and method in {
            "eth_getBlockByNumber",
            "trace_block",
        }:
            raise MethodError(
                f"{loc}: source {src} is for balance/call/gas/simulate/logs, not {method}"
            )
    contract = _parse_address(
        item.get("contract"), field="contract", source=loc
    ) or (default_contract if src == "known_contract" else None)
    raw_params = item.get("params")
    if raw_params is not None and src:
        raise MethodError(f"{loc}: use source or params, not both")
    if raw_params is None:
        params: tuple[Any, ...] = ()
    else:
        if not isinstance(raw_params, list):
            raise MethodError(f"{loc}.params must be a list")
        params = tuple(raw_params)
        if src is None:
            params = _default_params(method) if not params else params
    if src is None and not params:
        params = _default_params(method)
    raw_name = item.get("name")
    if raw_name is None:
        name = _SHORT_NAMES.get(method, method.split("_")[-1] or method)
    elif isinstance(raw_name, str) and raw_name.strip():
        name = raw_name.strip()
    else:
        raise MethodError(f"{loc}.name must be a non-empty string")
    optional = (
        method == "eth_simulateV1"
        or is_trace_method(method)
        or is_debug_trace_method(method)
    )
    raw_optional = item.get("optional")
    if raw_optional is not None:
        if not isinstance(raw_optional, bool):
            raise MethodError(f"{loc}.optional must be a boolean")
        optional = raw_optional or optional
    return CallSpec(
        name=name,
        method=method,
        params=params,
        weight=weight,
        source=src,
        contract=contract,
        optional=optional,
    )


def _default_params(method: str) -> tuple[Any, ...]:
    if method == "eth_getBlockByNumber":
        return ("latest", False)
    if method == "eth_getBalance":
        return (ZERO_ADDRESS, "latest")
    if method == "eth_call":
        return (dict(CALL_TX), "latest")
    if method == "eth_estimateGas":
        return (dict(CALL_TX),)
    if method == "eth_simulateV1":
        return simulate_params()
    if method == "eth_getLogs":
        return (
            {
                "fromBlock": "latest",
                "toBlock": "latest",
                "address": ZERO_ADDRESS,
            },
        )
    if method == "trace_block":
        return trace_block_params()
    if method == "trace_call":
        return trace_call_params()
    if method == "debug_traceCall":
        return debug_trace_call_params()
    return ()


def _reject_profile_method(method: str, *, allow_writes: bool) -> None:
    lower = method.lower()
    if is_trace_filter(method) or is_debug_recon(method):
        raise MethodError(f"{method} is not allowed in an app workload mix")
    if any(lower.startswith(p) for p in _PRIVILEGED_PREFIXES):
        raise MethodError(f"{method} is not allowed in an app workload mix")
    if not allow_writes:
        _reject_writes(method)


def _fill_params(
    spec: CallSpec,
    *,
    seed: int,
    index: int,
    head: int | None,
    chain_id: int | None,
    rng: random.Random,
) -> tuple[tuple[Any, ...], bool]:
    source = spec.source or ""
    fell = False
    block: str
    if source == "recent_block":
        if head is None:
            block = "latest"
            fell = True
        else:
            block = hex(recent_block(head, rng))
    elif source == "latest_head":
        if head is None:
            block = "latest"
            fell = True
        else:
            block = hex(head)
    else:
        block = "latest"

    if source == "seeded_address":
        address = seeded_address(seed, index)
    elif source == "known_contract":
        address = spec.contract or known_contract(chain_id)
        if address == ZERO_ADDRESS and spec.contract is None:
            fell = True
    else:
        address = ZERO_ADDRESS

    method = spec.method
    if method == "eth_getBlockByNumber":
        return (block, False), fell
    if method == "eth_getBalance":
        who = address if source in _ADDRESS_SOURCES else ZERO_ADDRESS
        tag = block if source in _BLOCK_SOURCES else "latest"
        return (who, tag), fell
    if method == "eth_call":
        to = address if source in _ADDRESS_SOURCES else ZERO_ADDRESS
        tag = block if source in _BLOCK_SOURCES else "latest"
        return ({"to": to, "data": "0x"}, tag), fell
    if method == "eth_estimateGas":
        to = address if source in _ADDRESS_SOURCES else ZERO_ADDRESS
        return ({"to": to, "data": "0x"},), fell
    if method == "eth_simulateV1":
        to = address if source in _ADDRESS_SOURCES else ZERO_ADDRESS
        tag = block if source in _BLOCK_SOURCES else "latest"
        return simulate_params(to=to, block=tag), fell
    if method == "eth_getLogs":
        addr = address if source in _ADDRESS_SOURCES else ZERO_ADDRESS
        tag = block if source in _BLOCK_SOURCES else "latest"
        return (
            {"fromBlock": tag, "toBlock": tag, "address": addr},
        ), fell
    if method == "trace_block":
        tag = block if source in _BLOCK_SOURCES else "latest"
        return trace_block_params(tag), fell
    if method == "trace_call":
        to = address if source in _ADDRESS_SOURCES else ZERO_ADDRESS
        tag = block if source in _BLOCK_SOURCES else "latest"
        return trace_call_params(to=to, block=tag), fell
    if method == "debug_traceCall":
        to = address if source in _ADDRESS_SOURCES else ZERO_ADDRESS
        tag = block if source in _BLOCK_SOURCES else "latest"
        return debug_trace_call_params(to=to, block=tag), fell
    return spec.params, fell
