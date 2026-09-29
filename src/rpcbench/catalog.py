"""Bundled public RPC catalog for ``--chain`` compare."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from rpcbench.config import BenchConfig, ConfigError, Endpoint, load_endpoints, parse_endpoints

_CATALOG_PACKAGE = "rpcbench.catalogs"
_ALIASES = {
    "eth": "ethereum",
    "mainnet": "ethereum",
    "ethereum-mainnet": "ethereum",
}


class CatalogError(ConfigError):
    """Unknown chain or bad catalog file."""


def normalize_chain(name: str) -> str:
    key = name.strip().lower().replace(" ", "-")
    if not key:
        raise CatalogError("chain name is required")
    return _ALIASES.get(key, key)


def list_chains() -> tuple[str, ...]:
    """Sorted chain ids that ship a catalog YAML in the package."""
    root = resources.files(_CATALOG_PACKAGE)
    names = sorted(
        path.stem
        for path in root.iterdir()
        if path.is_file()
        and path.suffix in {".yaml", ".yml"}
        and not path.name.startswith("_")
    )
    return tuple(names)


def catalog_meta(chain: str) -> dict:
    """Raw catalog mapping (version, family, endpoints, …)."""
    key = normalize_chain(chain)
    try:
        text = (
            resources.files(_CATALOG_PACKAGE)
            .joinpath(f"{key}.yaml")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, TypeError, OSError) as exc:
        available = ", ".join(list_chains()) or "(none)"
        raise CatalogError(
            f"unknown chain {chain!r}; bundled catalogs: {available}"
        ) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(f"invalid catalog for {key}: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogError(f"catalog {key}: expected a mapping")
    data = dict(data)
    data["_chain"] = key
    return data


def load_catalog(chain: str) -> BenchConfig:
    """Load the bundled endpoints for ``chain``."""
    meta = catalog_meta(chain)
    key = meta["_chain"]
    return parse_endpoints(meta, source=f"catalog:{key}")


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
) -> BenchConfig:
    """Combine catalog, optional file, and ``--endpoint`` URLs.

    Order: catalog, then file, then extras. Duplicate names get a numeric suffix.
    """
    if not chain and not endpoints_file and not extra_urls:
        raise ConfigError("pass --chain, --endpoints, or --endpoint")
    rows: list[Endpoint] = []
    if chain:
        rows.extend(load_catalog(chain).endpoints)
    if endpoints_file:
        rows.extend(load_endpoints(endpoints_file).endpoints)
    for url in extra_urls:
        rows.append(endpoint_from_url(url))
    return BenchConfig(endpoints=_unique_names(rows))


def catalog_banner(chain: str, config: BenchConfig) -> str:
    """One-line caveat for catalog compares."""
    meta = catalog_meta(chain)
    version = meta.get("version", "?")
    n = len(config.endpoints)
    return (
        f"catalog {normalize_chain(chain)} v{version}: {n} endpoint(s); "
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
