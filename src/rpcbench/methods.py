"""Read-only JSON-RPC methods used for latency probes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
# Solana system program. Cheap balance / account / signature reads.
SYSTEM_PROGRAM = "11111111111111111111111111111111"
FAMILY_EVM = "evm"
FAMILY_SOLANA = "solana"
FAMILY_SUBSTRATE = "substrate"
FAMILY_COSMOS = "cosmos"

# Presets: chain head, identity, and a cheap account read.
PRESETS: dict[str, tuple[str, list[Any]]] = {
    "head": ("eth_blockNumber", []),
    "chainId": ("eth_chainId", []),
    "balance": ("eth_getBalance", [ZERO_ADDRESS, "latest"]),
}
SOLANA_PRESETS: dict[str, tuple[str, list[Any]]] = {
    "head": ("getSlot", []),
    "chainId": ("getGenesisHash", []),
    "balance": ("getBalance", [SYSTEM_PROGRAM]),
}
SUBSTRATE_PRESETS: dict[str, tuple[str, list[Any]]] = {
    "head": ("chain_getHeader", []),
    "chainId": ("system_chain", []),
    "balance": ("state_getRuntimeVersion", []),
}
COSMOS_PRESETS: dict[str, tuple[str, list[Any]]] = {
    "head": ("status", []),
    "chainId": ("abci_info", []),
    "balance": ("num_unconfirmed_txs", []),
}

# Named app mixes. --profile mix is the old name for general.
APP_WORKLOADS = ("general", "wallet", "indexer", "trading", "nft", "tracing")
CORE_WORKLOADS = ("general", "wallet", "indexer", "trading", "nft")
PROFILE_ALIASES = {"mix": "general"}


@dataclass(frozen=True)
class CallSpec:
    """One fixed, documented RPC call in a workload."""

    name: str
    method: str
    params: tuple[Any, ...]
    weight: int = 1
    source: str | None = None
    contract: str | None = None
    optional: bool = False


@dataclass(frozen=True)
class WorkloadPlan:
    """Resolved mix or single method. Unpackable as (label, steps)."""

    label: str
    steps: tuple[CallSpec, ...]
    notes: str | None = None
    timeout: float | None = None

    def __iter__(self) -> Iterator[object]:
        yield self.label
        yield self.steps


# Bounded logs: one block, one address. No scan. Used only where the mix needs it.
_LOGS_FILTER = {
    "fromBlock": "latest",
    "toBlock": "latest",
    "address": ZERO_ADDRESS,
}

# Read-only simulation fixture. Empty calldata, zero address, no value.
# Never a signed tx. estimateGas / simulateV1 / eth_call share this object.
CALL_TX: dict[str, str] = {"to": ZERO_ADDRESS, "data": "0x"}


def simulate_params(
    *, to: str = ZERO_ADDRESS, block: str = "latest"
) -> tuple[dict[str, object], str]:
    """One-call eth_simulateV1 payload. validation=false so it is not a send."""
    return (
        {
            "blockStateCalls": [{"calls": [{"to": to, "data": "0x"}]}],
            "traceTransfers": False,
            "validation": False,
        },
        block,
    )


def _head(weight: int = 1) -> CallSpec:
    return CallSpec("head", "eth_blockNumber", (), weight)


def _chain_id(weight: int = 1) -> CallSpec:
    return CallSpec("chainId", "eth_chainId", (), weight)


def _block(weight: int = 1) -> CallSpec:
    return CallSpec("block", "eth_getBlockByNumber", ("latest", False), weight)


def _balance(weight: int = 1) -> CallSpec:
    return CallSpec("balance", "eth_getBalance", (ZERO_ADDRESS, "latest"), weight)


def _call(weight: int = 1) -> CallSpec:
    return CallSpec(
        "call",
        "eth_call",
        (dict(CALL_TX), "latest"),
        weight,
    )


def _gas(weight: int = 1) -> CallSpec:
    return CallSpec("gas", "eth_estimateGas", (dict(CALL_TX),), weight)


def _simulate(weight: int = 1) -> CallSpec:
    return CallSpec(
        "simulate",
        "eth_simulateV1",
        simulate_params(),
        weight,
        optional=True,
    )


def _logs(weight: int = 1) -> CallSpec:
    return CallSpec("logs", "eth_getLogs", (_LOGS_FILTER,), weight)


def _sol_head(weight: int = 1) -> CallSpec:
    return CallSpec("head", "getSlot", (), weight)


def _sol_genesis(weight: int = 1) -> CallSpec:
    return CallSpec("genesis", "getGenesisHash", (), weight)


def _sol_blockhash(weight: int = 1) -> CallSpec:
    return CallSpec("blockhash", "getLatestBlockhash", (), weight)


def _sol_balance(weight: int = 1) -> CallSpec:
    return CallSpec("balance", "getBalance", (SYSTEM_PROGRAM,), weight)


def _sol_account(weight: int = 1) -> CallSpec:
    return CallSpec(
        "account",
        "getAccountInfo",
        (SYSTEM_PROGRAM, {"encoding": "base64"}),
        weight,
    )


def _sol_signatures(weight: int = 1) -> CallSpec:
    return CallSpec(
        "signatures",
        "getSignaturesForAddress",
        (SYSTEM_PROGRAM, {"limit": 1}),
        weight,
    )


def _sub_head(weight: int = 1) -> CallSpec:
    return CallSpec("head", "chain_getHeader", (), weight)


def _sub_chain(weight: int = 1) -> CallSpec:
    return CallSpec("chain", "system_chain", (), weight)


def _sub_hash(weight: int = 1) -> CallSpec:
    # null = best/latest finalized-aware tip hash on most nodes.
    return CallSpec("hash", "chain_getBlockHash", (None,), weight)


def _sub_runtime(weight: int = 1) -> CallSpec:
    return CallSpec("runtime", "state_getRuntimeVersion", (), weight)


def _sub_health(weight: int = 1) -> CallSpec:
    return CallSpec("health", "system_health", (), weight)


def _sub_sync(weight: int = 1) -> CallSpec:
    return CallSpec("sync", "system_syncState", (), weight)


def _cos_head(weight: int = 1) -> CallSpec:
    return CallSpec("head", "status", (), weight)


def _cos_info(weight: int = 1) -> CallSpec:
    return CallSpec("info", "abci_info", (), weight)


def _cos_block(weight: int = 1) -> CallSpec:
    # null height = latest tip on CometBFT JSON-RPC.
    return CallSpec("block", "block", (None,), weight)


def _cos_net(weight: int = 1) -> CallSpec:
    return CallSpec("net", "net_info", (), weight)


def _cos_mempool(weight: int = 1) -> CallSpec:
    return CallSpec("mempool", "num_unconfirmed_txs", (), weight)


def _cos_consensus(weight: int = 1) -> CallSpec:
    return CallSpec("consensus", "consensus_state", (), weight)


# Cheap one-block trace. ["trace"] only — not vmTrace / stateDiff / trace_filter.
TRACE_TYPES = ("trace",)


def trace_block_params(block: str = "latest") -> tuple[str]:
    return (block,)


def trace_call_params(
    *, to: str = ZERO_ADDRESS, block: str = "latest"
) -> tuple[dict[str, str], list[str], str]:
    return ({"to": to, "data": "0x"}, list(TRACE_TYPES), block)


def _trace(weight: int = 1) -> CallSpec:
    return CallSpec(
        "trace",
        "trace_block",
        trace_block_params(),
        weight,
        optional=True,
    )


def is_trace_method(method: str) -> bool:
    return method.lower().startswith("trace_")


def is_trace_filter(method: str) -> bool:
    return method.lower().startswith("trace_filter")


# Cheap Geth-style debug execution. callTracer + 1s cap — not memStats / verbosity.
DEBUG_TRACER = "callTracer"
DEBUG_TRACE_TIMEOUT = "1s"
DEBUG_TRACE_METHODS = frozenset({"debug_traceCall"})


def debug_trace_call_params(
    *, to: str = ZERO_ADDRESS, block: str = "latest"
) -> tuple[dict[str, str], str, dict[str, str]]:
    return (
        {"to": to, "data": "0x"},
        block,
        {"tracer": DEBUG_TRACER, "timeout": DEBUG_TRACE_TIMEOUT},
    )


def _debug(weight: int = 1) -> CallSpec:
    return CallSpec(
        "debug",
        "debug_traceCall",
        debug_trace_call_params(),
        weight,
        optional=True,
    )


def is_debug_trace_method(method: str) -> bool:
    return method.lower() in {name.lower() for name in DEBUG_TRACE_METHODS}


def is_debug_recon(method: str) -> bool:
    """True for debug_* that is not the cheap timed call (Nodeprobe's lane)."""
    return method.lower().startswith("debug_") and not is_debug_trace_method(method)


# EVM catalogs. Privileged admin never belongs here.
# tracing is the only catalog that times trace_* / debug_traceCall.
_EVM_WORKLOADS: dict[str, tuple[CallSpec, ...]] = {
    "general": (
        _head(),
        _chain_id(),
        _block(),
        _balance(),
        _call(),
        _logs(),
    ),
    "wallet": (
        _head(),
        _chain_id(),
        _block(),
        _balance(4),
        _call(3),
        _gas(2),
    ),
    "indexer": (
        _head(),
        _chain_id(),
        _block(3),
        _call(),
        _logs(4),
    ),
    "trading": (
        _head(3),
        _chain_id(),
        _block(2),
        _call(4),
        _gas(2),
    ),
    "nft": (
        _head(),
        _chain_id(),
        _block(),
        _balance(),
        _call(3),
        _logs(3),
    ),
    "tracing": (
        _head(),
        _chain_id(),
        _block(),
        _trace(4),
        _debug(2),
    ),
}

# Solana catalogs. No eth_*, no Geyser. tracing stays EVM-only.
_SOLANA_WORKLOADS: dict[str, tuple[CallSpec, ...]] = {
    "general": (
        _sol_head(),
        _sol_genesis(),
        _sol_blockhash(),
        _sol_balance(),
        _sol_account(),
        _sol_signatures(),
    ),
    "wallet": (
        _sol_head(),
        _sol_genesis(),
        _sol_blockhash(),
        _sol_balance(4),
        _sol_account(3),
        _sol_blockhash(2),
    ),
    "indexer": (
        _sol_head(),
        _sol_genesis(),
        _sol_blockhash(3),
        _sol_account(),
        _sol_signatures(4),
    ),
    "trading": (
        _sol_head(3),
        _sol_genesis(),
        _sol_blockhash(2),
        _sol_account(4),
        _sol_balance(2),
    ),
    "nft": (
        _sol_head(),
        _sol_genesis(),
        _sol_blockhash(),
        _sol_balance(),
        _sol_account(3),
        _sol_signatures(3),
    ),
}

# Substrate catalogs. One adapter for Polkadot/Kusama/parachains. No author_*.
_SUBSTRATE_WORKLOADS: dict[str, tuple[CallSpec, ...]] = {
    "general": (
        _sub_head(),
        _sub_chain(),
        _sub_hash(),
        _sub_runtime(),
        _sub_health(),
        _sub_sync(),
    ),
    "wallet": (
        _sub_head(),
        _sub_chain(),
        _sub_hash(),
        _sub_runtime(4),
        _sub_health(3),
        _sub_hash(2),
    ),
    "indexer": (
        _sub_head(),
        _sub_chain(),
        _sub_hash(3),
        _sub_runtime(),
        _sub_sync(4),
    ),
    "trading": (
        _sub_head(3),
        _sub_chain(),
        _sub_hash(2),
        _sub_runtime(4),
        _sub_health(2),
    ),
    "nft": (
        _sub_head(),
        _sub_chain(),
        _sub_hash(),
        _sub_runtime(),
        _sub_health(3),
        _sub_sync(3),
    ),
}

# Cosmos / CometBFT catalogs. Not Cosmos EVM eth_*. tracing stays EVM-only.
# abci_query / tx_search need named object params — deferred.
_COSMOS_WORKLOADS: dict[str, tuple[CallSpec, ...]] = {
    "general": (
        _cos_head(),
        _cos_info(),
        _cos_block(),
        _cos_net(),
        _cos_mempool(),
        _cos_consensus(),
    ),
    "wallet": (
        _cos_head(),
        _cos_info(),
        _cos_block(),
        _cos_mempool(4),
        _cos_net(3),
        _cos_block(2),
    ),
    "indexer": (
        _cos_head(),
        _cos_info(),
        _cos_block(3),
        _cos_net(),
        _cos_consensus(4),
    ),
    "trading": (
        _cos_head(3),
        _cos_info(),
        _cos_block(2),
        _cos_mempool(4),
        _cos_net(2),
    ),
    "nft": (
        _cos_head(),
        _cos_info(),
        _cos_block(),
        _cos_mempool(),
        _cos_net(3),
        _cos_consensus(3),
    ),
}

# Family → named mix. Missing catalogs error instead of sending eth_* elsewhere.
WORKLOADS: dict[str, dict[str, tuple[CallSpec, ...]]] = {
    FAMILY_EVM: _EVM_WORKLOADS,
    FAMILY_SOLANA: _SOLANA_WORKLOADS,
    FAMILY_SUBSTRATE: _SUBSTRATE_WORKLOADS,
    FAMILY_COSMOS: _COSMOS_WORKLOADS,
}

# Default mix: head, identity, block fetch, state, call, bounded logs.
# Payloads are chain-agnostic (zero address, latest-only logs).
MIX_PROFILE: tuple[CallSpec, ...] = _EVM_WORKLOADS["general"]

_WRITE_PREFIXES = (
    "eth_send",
    "eth_sign",
    "personal_",
    "miner_",
    "admin_",
    "wallet_",
)
_SOLANA_WRITE_METHODS = frozenset(
    {
        "sendtransaction",
        "sendandsigntransaction",
        "requestairdrop",
    }
)
_SUBSTRATE_WRITE_METHODS = frozenset(
    {
        "author_submitextrinsic",
        "author_submitandwatchextrinsic",
    }
)
_COSMOS_WRITE_METHODS = frozenset(
    {
        "broadcast_tx_sync",
        "broadcast_tx_async",
        "broadcast_tx_commit",
        "broadcast_evidence",
    }
)

# Privileged namespaces. App mixes must never use these as a probe.
# trace_* / debug_traceCall are opt-in (--workload tracing / YAML).
# trace_filter and other debug_* (memStats, verbosity, …) stay rejected.
_FORBIDDEN_MIX_PREFIXES = (
    *_WRITE_PREFIXES,
    "engine_",
    "txpool_",
    "clique_",
)


class MethodError(ValueError):
    pass


_RPC_NAMESPACES = {
    "eth",
    "net",
    "web3",
    "debug",
    "trace",
    "txpool",
    "engine",
    "admin",
    "personal",
    "miner",
    "wallet",
    "clique",
    "rpc",
}


def is_app_workload(profile: str | None) -> bool:
    """True for catalog mixes, the mix alias, and custom YAML profile names."""
    if not profile:
        return False
    key = profile.strip().lower()
    if key == "single":
        return False
    if key in APP_WORKLOADS or key in PROFILE_ALIASES:
        return True
    return not _looks_like_rpc_method(key)


def _looks_like_rpc_method(name: str) -> bool:
    if "_" not in name:
        return False
    return name.split("_", 1)[0].lower() in _RPC_NAMESPACES


def canonical_workload(name: str) -> str | None:
    """Map mix/general/wallet/… to a catalog key, or None if unknown."""
    key = name.strip().lower()
    if key in PROFILE_ALIASES:
        return PROFILE_ALIASES[key]
    if key in APP_WORKLOADS:
        return key
    return None


def request_units(workload: tuple[CallSpec, ...]) -> int:
    """HTTP calls per endpoint per sample/warmup round (sum of weights)."""
    return sum(max(1, spec.weight) for spec in workload)


def apply_simulate(steps: tuple[CallSpec, ...]) -> tuple[CallSpec, ...]:
    """Append missing read-only simulation steps. simulateV1 is optional."""
    methods = {spec.method for spec in steps}
    extra: list[CallSpec] = []
    if "eth_call" not in methods:
        extra.append(_call())
    if "eth_estimateGas" not in methods:
        extra.append(_gas())
    if "eth_simulateV1" not in methods:
        extra.append(_simulate())
    return steps + tuple(extra)


def _presets_for(family: str) -> dict[str, tuple[str, list[Any]]]:
    if family == FAMILY_SOLANA:
        return SOLANA_PRESETS
    if family == FAMILY_SUBSTRATE:
        return SUBSTRATE_PRESETS
    if family == FAMILY_COSMOS:
        return COSMOS_PRESETS
    return PRESETS


def _default_method(family: str) -> str:
    if family == FAMILY_SOLANA:
        return "getSlot"
    if family == FAMILY_SUBSTRATE:
        return "chain_getHeader"
    if family == FAMILY_COSMOS:
        return "status"
    return "eth_blockNumber"


def resolve_method(
    *,
    method: str | None,
    preset: str | None,
    params_json: str | None,
    allow_writes: bool = False,
    family: str = FAMILY_EVM,
) -> tuple[str, list[Any]]:
    if preset and method:
        raise MethodError("use either --preset or --method, not both")
    packs = _presets_for(family)
    if preset:
        key = preset.strip().lower()
        matched = next((name for name in packs if name.lower() == key), None)
        if matched is None:
            known = ", ".join(sorted(packs))
            raise MethodError(f"unknown preset {preset!r} (try {known})")
        name, params = packs[matched]
        if params_json:
            params = parse_params(params_json)
        if not allow_writes:
            _reject_writes(name)
        return name, params
    name = (method or _default_method(family)).strip()
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
    workload: str | None = None,
    family: str = FAMILY_EVM,
) -> WorkloadPlan:
    """Return a plan. Unpackable as (label, steps)."""
    path = _custom_profile_path(profile=profile, workload=workload)
    if path is not None:
        if method or preset:
            raise MethodError(
                "use --workload or --profile without --method or --preset"
            )
        if params_json:
            raise MethodError("mix payloads are fixed; do not pass --params")
        if profile and workload:
            raise MethodError("use --workload or --profile, not both")
        from rpcbench.profile import load_profile

        return load_profile(path, allow_writes=allow_writes)
    chosen = _pick_mix_label(profile=profile, workload=workload)
    if chosen is not None:
        if method or preset:
            raise MethodError(
                "use --workload or --profile without --method or --preset"
            )
        if params_json:
            raise MethodError("mix payloads are fixed; do not pass --params")
        steps = family_workload(family, chosen)
        for spec in steps:
            if not allow_writes:
                _reject_writes(spec.method)
            _reject_mix_probe(spec.method)
        return WorkloadPlan(label=chosen, steps=steps)
    name, params = resolve_method(
        method=method,
        preset=preset,
        params_json=params_json,
        allow_writes=allow_writes,
        family=family,
    )
    step = "call"
    if preset:
        packs = _presets_for(family)
        matched = next(
            (n for n in packs if n.lower() == preset.strip().lower()), None
        )
        if matched:
            step = matched
    elif name == _default_method(family) and not params:
        step = "head"
    return WorkloadPlan(label=name, steps=(CallSpec(step, name, tuple(params)),))


def family_workload(family: str, name: str) -> tuple[CallSpec, ...]:
    """Named mix for this RPC family."""
    catalog = canonical_workload(name)
    if catalog is None:
        known = ", ".join(("mix",) + APP_WORKLOADS)
        raise MethodError(f"unknown --workload {name!r} (try {known})")
    families = WORKLOADS.get(family)
    if families is None:
        have = ", ".join(sorted(WORKLOADS))
        raise MethodError(
            f"app workloads have no mix for family {family!r} (have: {have})"
        )
    if catalog not in families:
        have = ", ".join(sorted(families))
        raise MethodError(
            f"workload {catalog!r} has no {family} mix yet (have: {have})"
        )
    return families[catalog]


def parse_params(raw: str) -> list[Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MethodError(f"invalid --params JSON: {exc}") from exc
    if not isinstance(data, list):
        raise MethodError("--params must be a JSON array")
    return data


def _custom_profile_path(*, profile: str | None, workload: str | None) -> Path | None:
    from rpcbench.profile import as_profile_path

    left = as_profile_path(profile)
    right = as_profile_path(workload)
    return left or right


def _pick_mix_label(*, profile: str | None, workload: str | None) -> str | None:
    if not profile and not workload:
        return None
    if profile and workload:
        left = canonical_workload(profile)
        right = canonical_workload(workload)
        if left is None:
            known = ", ".join(("mix",) + APP_WORKLOADS)
            raise MethodError(
                f"unknown --profile {profile!r} (try {known} or a YAML file)"
            )
        if right is None:
            known = ", ".join(("mix",) + APP_WORKLOADS)
            raise MethodError(f"unknown --workload {workload!r} (try {known})")
        if left != right:
            raise MethodError("use --workload or --profile, not both")
        return workload.strip().lower()
    raw = (workload or profile or "").strip()
    flag = "--workload" if workload else "--profile"
    key = raw.lower()
    if canonical_workload(key) is None:
        known = ", ".join(("mix",) + APP_WORKLOADS)
        hint = f"{known} or a YAML file" if flag == "--profile" else known
        raise MethodError(f"unknown {flag} {raw!r} (try {hint})")
    return key


def is_write_method(method: str) -> bool:
    lower = method.lower()
    if any(lower.startswith(p) for p in _WRITE_PREFIXES):
        return True
    return (
        lower in _SOLANA_WRITE_METHODS
        or lower in _SUBSTRATE_WRITE_METHODS
        or lower in _COSMOS_WRITE_METHODS
    )


def _reject_writes(method: str) -> None:
    if is_write_method(method):
        raise MethodError(
            f"{method} is a write method; pass --allow-writes to run it anyway"
        )


def _reject_mix_probe(method: str) -> None:
    lower = method.lower()
    if is_trace_filter(method) or is_debug_recon(method):
        raise MethodError(f"{method} is not allowed in an app workload mix")
    if any(lower.startswith(p) for p in _FORBIDDEN_MIX_PREFIXES):
        raise MethodError(f"{method} is not allowed in an app workload mix")
