"""Benchmark family adapter. Picks a mix, not a scan ruleset.

Identity handshakes (eth_chainId, getHealth, system_health) choose a family.
They are not findings. Unknown EVM chain IDs still use the one EVM adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rpcbench.config import ConfigError
from rpcbench.freshness import parse_block_height

if TYPE_CHECKING:
    import httpx

    from rpcbench.config import BenchConfig, Endpoint
    from rpcbench.rpc import ProbeResult

FAMILY_EVM = "evm"
FAMILY_SOLANA = "solana"
FAMILY_SUBSTRATE = "substrate"
FAMILY_AUTO = "auto"

# Declared so a typo and a future family fail differently.
KNOWN_FAMILIES = (
    "evm",
    "solana",
    "substrate",
    "cosmos",
    "aptos",
    "sui",
    "near",
    "starknet",
    "bitcoin",
    "ton",
    "auto",
)
IMPLEMENTED = frozenset({FAMILY_EVM, FAMILY_SOLANA, FAMILY_SUBSTRATE})

# Identity only. No admin/personal/engine/txpool and no method inventory.
_DETECT_PROBES = (
    ("eth_chainId", FAMILY_EVM),
    ("getHealth", FAMILY_SOLANA),
    ("system_health", FAMILY_SUBSTRATE),
)

_SOLANA_BLOCK_CONFIG: dict[str, Any] = {
    "encoding": "json",
    "transactionDetails": "none",
    "rewards": False,
    "maxSupportedTransactionVersion": 0,
}


@dataclass(frozen=True)
class FamilyAdapter:
    """What to time for one protocol family. Not a per-chain engine."""

    name: str
    head_method: str
    chain_method: str
    block_method: str
    block_time_s: float
    batch_shape: str
    ws_method: str
    ws_params: tuple[Any, ...]
    client_method: str
    # Empty means the EVM latest/safe/finalized tag wave is skipped.
    block_tags: tuple[str, ...] = ()


EVM = FamilyAdapter(
    name=FAMILY_EVM,
    head_method="eth_blockNumber",
    chain_method="eth_chainId",
    block_method="eth_getBlockByNumber",
    block_time_s=12.0,
    batch_shape="jsonrpc-array",
    ws_method="eth_subscribe",
    ws_params=("newHeads",),
    client_method="web3_clientVersion",
    block_tags=("latest", "safe", "finalized"),
)

SOLANA = FamilyAdapter(
    name=FAMILY_SOLANA,
    head_method="getSlot",
    chain_method="getGenesisHash",
    block_method="getBlock",
    block_time_s=0.4,
    batch_shape="jsonrpc-array",
    ws_method="slotSubscribe",
    ws_params=(),
    client_method="getVersion",
)

SUBSTRATE = FamilyAdapter(
    name=FAMILY_SUBSTRATE,
    head_method="chain_getHeader",
    chain_method="system_chain",
    block_method="chain_getBlockHash",
    block_time_s=6.0,
    batch_shape="jsonrpc-array",
    ws_method="chain_subscribeNewHeads",
    ws_params=(),
    client_method="system_version",
)


def normalize_family(raw: object) -> str:
    """Config value. Omitted means evm. ``auto`` detects. Others must be known."""
    if raw is None:
        return FAMILY_EVM
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(
            "family must be a name "
            f"({', '.join(KNOWN_FAMILIES)})"
        )
    key = raw.strip().lower()
    if key not in KNOWN_FAMILIES:
        known = ", ".join(KNOWN_FAMILIES)
        raise ConfigError(f"unknown family {raw!r} (try {known})")
    if key not in IMPLEMENTED and key != FAMILY_AUTO:
        have = ", ".join(sorted(IMPLEMENTED))
        raise ConfigError(
            f"family {key!r} has no benchmark mix yet (implemented: {have})"
        )
    return key


def benchmark_family(name: str) -> FamilyAdapter:
    """The one adapter for this family. EVM is not split by chain id."""
    key = normalize_family(name)
    if key == FAMILY_AUTO:
        raise ConfigError("family auto must be resolved before a benchmark")
    if key == FAMILY_EVM:
        return EVM
    if key == FAMILY_SOLANA:
        return SOLANA
    if key == FAMILY_SUBSTRATE:
        return SUBSTRATE
    have = ", ".join(sorted(IMPLEMENTED))
    raise ConfigError(
        f"family {key!r} has no benchmark mix yet (implemented: {have})"
    )


def head_method_for(family: str) -> str:
    return benchmark_family(family).head_method


def pin_block_params(adapter: FamilyAdapter, pin: int) -> list[Any]:
    """Params for the consistency pin read. Shape is family-specific."""
    if adapter.name == FAMILY_SOLANA:
        return [pin, dict(_SOLANA_BLOCK_CONFIG)]
    if adapter.name == FAMILY_SUBSTRATE:
        return [hex(pin)]
    return [hex(pin), False]


def tag_block_params(adapter: FamilyAdapter, tag: str) -> list[Any]:
    if adapter.name in {FAMILY_SOLANA, FAMILY_SUBSTRATE}:
        raise ConfigError(f"{adapter.name} has no eth-style block tags")
    return [tag, False]


def resolve_block_time(
    adapter: FamilyAdapter,
    *,
    chain_id: int | None,
    override: float | None,
) -> float:
    """Seconds per head unit. Solana uses slot time; EVM uses chain id tables."""
    if override is not None:
        return override
    if adapter.name == FAMILY_EVM:
        from rpcbench.freshness import block_time_for_chain

        return block_time_for_chain(chain_id, None)
    return adapter.block_time_s


def meta_requests_for(family: str) -> int:
    """Client version plus optional EVM tag snapshots. Not ranking samples."""
    adapter = benchmark_family(family)
    return 1 + len(adapter.block_tags)


def resolve_benchmark_family(
    config: BenchConfig,
    *,
    override: str | None = None,
    timeout: float = 10.0,
    client: httpx.Client | None = None,
) -> str:
    """One implemented family for the run. ``--family`` overrides the file."""
    forced = normalize_family(override) if override else None
    resolved: list[str] = []
    for endpoint in config.endpoints:
        name = forced if forced is not None else endpoint.family
        if name == FAMILY_AUTO:
            name = detect_family(endpoint, timeout=timeout, client=client)
        if name not in IMPLEMENTED:
            have = ", ".join(sorted(IMPLEMENTED))
            raise ConfigError(
                f"family {name!r} has no benchmark mix yet (implemented: {have})"
            )
        resolved.append(name)
    if len(set(resolved)) > 1:
        raise ConfigError(
            "endpoints resolved to more than one family; compare one family at a time"
        )
    return resolved[0] if resolved else FAMILY_EVM


def detect_family(
    endpoint: Endpoint,
    *,
    timeout: float,
    client: httpx.Client | None = None,
) -> str:
    """Cheap identity handshake. A hit names a family. It is not a finding."""
    from rpcbench.rpc import probe

    for method, family in _DETECT_PROBES:
        hit = probe(
            endpoint.url,
            method,
            params=[],
            timeout=timeout,
            retries=0,
            client=client,
            headers=endpoint.headers,
        )
        if _identity_hit(method, hit):
            return family
    tried = ", ".join(method for method, _family in _DETECT_PROBES)
    raise ConfigError(
        f"could not detect a family for {endpoint.name} (tried {tried}); "
        "set family: evm"
    )


def _identity_hit(method: str, hit: ProbeResult) -> bool:
    if not hit.ok or hit.result is None:
        return False
    if method == "eth_chainId":
        # Any chain id, including ones we have never listed, is still EVM.
        return parse_block_height(hit.result) is not None
    if method == "getHealth":
        return hit.result == "ok"
    if method == "system_health":
        return isinstance(hit.result, dict)
    return False
