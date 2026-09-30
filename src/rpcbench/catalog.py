"""Live public RPC catalog for ``--chain`` (Chainlist fetch, no hardcoded URLs)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from rpcbench.config import BenchConfig, ConfigError, Endpoint, load_endpoints, parse_endpoints

# Chainlist public dump (DefiLlama). Refreshed on each --chain run.
CHAINLIST_URL = "https://chainlist.org/rpcs.json"
DEFAULT_CHAIN_LIMIT = 12
DEFAULT_FETCH_TIMEOUT = 20.0

# Name → EVM chain id / family. No RPC URLs here — those come from Chainlist.
_CHAINS: dict[str, dict[str, Any]] = {
    "ethereum": {"chain_id": 1, "family": "evm"},
}
_ALIASES = {
    "eth": "ethereum",
    "mainnet": "ethereum",
    "ethereum-mainnet": "ethereum",
}

_KEY_PLACEHOLDER = re.compile(
    r"(api[_-]?key|apikey|\{key\}|\$\{|/demo(?:/|$)|YOUR[_-]|<key>)",
    re.IGNORECASE,
)

FetchFn = Callable[[str, float], Any]


class CatalogError(ConfigError):
    """Unknown chain or failed catalog fetch."""


def normalize_chain(name: str) -> str:
    key = name.strip().lower().replace(" ", "-")
    if not key:
        raise CatalogError("chain name is required")
    return _ALIASES.get(key, key)


def list_chains() -> tuple[str, ...]:
    """Chains with a known chain-id mapping (endpoints still come from Chainlist)."""
    return tuple(sorted(_CHAINS))


def chain_info(chain: str) -> dict[str, Any]:
    key = normalize_chain(chain)
    info = _CHAINS.get(key)
    if info is None:
        available = ", ".join(list_chains()) or "(none)"
        raise CatalogError(
            f"unknown chain {chain!r}; supported: {available}"
        )
    return {"_chain": key, **info}


def fetch_chainlist(url: str = CHAINLIST_URL, timeout: float = DEFAULT_FETCH_TIMEOUT) -> Any:
    """GET Chainlist JSON. Injectable in tests via ``load_catalog(..., fetch=...)``."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url, headers={"user-agent": "rpcbench"})
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CatalogError(f"failed to fetch catalog from {url}: {exc}") from exc


def _rpc_url(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict):
        raw = entry.get("url")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _is_keyless_https(url: str) -> bool:
    scheme = url.split(":", 1)[0].lower()
    if scheme != "https":
        return False
    if _KEY_PLACEHOLDER.search(url):
        return False
    if "${" in url or "{{" in url:
        return False
    return True


def _host_label(url: str) -> str:
    host = urlsplit(url).hostname or "endpoint"
    # Drop leading www.; keep multi-part hosts readable.
    if host.startswith("www."):
        host = host[4:]
    return host.replace(".", "-")


def endpoints_from_chainlist(
    data: Any,
    *,
    chain_id: int,
    family: str,
    limit: int = DEFAULT_CHAIN_LIMIT,
) -> list[dict[str, str]]:
    """Pick keyless HTTPS RPCs for ``chain_id`` from a Chainlist payload."""
    if not isinstance(data, list):
        raise CatalogError("chainlist payload must be a list of chains")
    match: dict[str, Any] | None = None
    for row in data:
        if isinstance(row, dict) and row.get("chainId") == chain_id:
            match = row
            break
    if match is None:
        raise CatalogError(f"chain id {chain_id} not found in Chainlist")
    rpcs = match.get("rpc") or []
    if not isinstance(rpcs, list):
        raise CatalogError(f"chain id {chain_id}: rpc list missing")
    seen_hosts: set[str] = set()
    out: list[dict[str, str]] = []
    for entry in rpcs:
        url = _rpc_url(entry)
        if url is None or not _is_keyless_https(url):
            continue
        host = (urlsplit(url).hostname or "").lower()
        if not host or host in seen_hosts:
            continue
        seen_hosts.add(host)
        out.append({"name": _host_label(url), "url": url, "family": family})
        if limit > 0 and len(out) >= limit:
            break
    if not out:
        raise CatalogError(
            f"no keyless https RPCs for chain id {chain_id} in Chainlist"
        )
    return out


def load_catalog(
    chain: str,
    *,
    limit: int = DEFAULT_CHAIN_LIMIT,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
    fetch: FetchFn | None = None,
    source_url: str = CHAINLIST_URL,
) -> BenchConfig:
    """Fetch Chainlist and build endpoints for ``chain`` (no bundled URL list)."""
    info = chain_info(chain)
    key = info["_chain"]
    opener = fetch or fetch_chainlist
    data = opener(source_url, timeout)
    items = endpoints_from_chainlist(
        data,
        chain_id=int(info["chain_id"]),
        family=str(info["family"]),
        limit=limit,
    )
    return parse_endpoints({"endpoints": items}, source=f"chainlist:{key}")


def endpoint_from_url(url: str, *, name: str | None = None) -> Endpoint:
    """Build one Endpoint from an http(s) URL (``--endpoint``)."""
    text = url.strip()
    if not text:
        raise ConfigError("endpoint URL is required")
    scheme = text.split(":", 1)[0].lower()
    if scheme not in {"http", "https"}:
        raise ConfigError(f"endpoint URL must be http or https, got {scheme!r}")
    host = urlsplit(text).hostname or "endpoint"
    label = (name or host).strip()
    if not label:
        label = "endpoint"
    return parse_endpoints(
        {"endpoints": [{"name": label, "url": text}]},
        source="--endpoint",
    ).endpoints[0]


def merge_targets(
    *,
    chain: str | None = None,
    endpoints_file: str | Path | None = None,
    extra_urls: tuple[str, ...] | list[str] = (),
    limit: int = DEFAULT_CHAIN_LIMIT,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
    fetch: FetchFn | None = None,
) -> BenchConfig:
    """Combine live catalog, optional file, and ``--endpoint`` URLs."""
    if not chain and not endpoints_file and not extra_urls:
        raise ConfigError("pass --chain, --endpoints, or --endpoint")
    rows: list[Endpoint] = []
    if chain:
        rows.extend(
            load_catalog(chain, limit=limit, timeout=timeout, fetch=fetch).endpoints
        )
    if endpoints_file:
        rows.extend(load_endpoints(endpoints_file).endpoints)
    for url in extra_urls:
        rows.append(endpoint_from_url(url))
    return BenchConfig(endpoints=_unique_names(rows))


def catalog_banner(chain: str, config: BenchConfig) -> str:
    """One-line caveat for catalog compares."""
    key = normalize_chain(chain)
    n = len(config.endpoints)
    return (
        f"chainlist {key}: {n} endpoint(s) fetched live; "
        f"public RPCs rate-limit — default budget is short, not long. "
        f"See docs/CATALOG.md"
    )


def _unique_names(endpoints: list[Endpoint]) -> tuple[Endpoint, ...]:
    seen: dict[str, int] = {}
    out: list[Endpoint] = []
    for endpoint in endpoints:
        base = endpoint.name
        count = seen.get(base, 0)
        seen[base] = count + 1
        name = base if count == 0 else f"{base}-{count + 1}"
        if name != endpoint.name:
            endpoint = Endpoint(
                name=name,
                url=endpoint.url,
                headers=endpoint.headers,
                ws_url=endpoint.ws_url,
                grpc_url=endpoint.grpc_url,
                family=endpoint.family,
            )
        out.append(endpoint)
    return tuple(out)
