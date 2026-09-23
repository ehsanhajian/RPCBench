"""Load named RPC endpoints from YAML or JSON. Localhost is allowed."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from rpcbench.urls import display_url, url_fingerprint


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    ws_url: str | None = None
    family: str = "evm"

    @property
    def display_url(self) -> str:
        return display_url(self.url)

    @property
    def display_ws_url(self) -> str | None:
        if not self.ws_url:
            return None
        return display_url(self.ws_url)

    @property
    def url_id(self) -> str:
        return url_fingerprint(self.url)


@dataclass(frozen=True)
class BenchConfig:
    endpoints: tuple[Endpoint, ...]


def load_targets(spec: str | Path) -> BenchConfig:
    text = str(spec).strip()
    if text.startswith("http://") or text.startswith("https://"):
        host = urlsplit(text).hostname or "cli"
        return parse_endpoints(
            {"endpoints": [{"name": host, "url": text}]},
            source="cli",
        )
    return load_endpoints(text)


def load_endpoints(path: str | Path) -> BenchConfig:
    raw_path = Path(path)
    if not raw_path.is_file():
        raise ConfigError(f"endpoints file not found: {raw_path}")
    text = raw_path.read_text(encoding="utf-8")
    suffix = raw_path.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid endpoints file {raw_path}: {exc}") from exc
    return parse_endpoints(data, source=str(raw_path))


def parse_endpoints(data: object, *, source: str = "config") -> BenchConfig:
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: expected a mapping with an 'endpoints' list")
    items = data.get("endpoints")
    if not isinstance(items, list) or not items:
        raise ConfigError(f"{source}: 'endpoints' must be a non-empty list")
    seen: set[str] = set()
    endpoints: list[Endpoint] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f"{source}: endpoints[{i}] must be a mapping")
        name = item.get("name")
        url = item.get("url")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{source}: endpoints[{i}].name is required")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError(f"{source}: endpoints[{i}].url is required")
        name = name.strip()
        url = url.strip()
        if name in seen:
            raise ConfigError(f"{source}: duplicate endpoint name {name!r}")
        scheme = url.split(":", 1)[0].lower()
        if scheme not in {"http", "https"}:
            raise ConfigError(
                f"{source}: endpoints[{i}] ({name}) URL must be http or https"
            )
        headers = _parse_headers(item, source=source, index=i, name=name)
        ws_url = _parse_ws_url(item, source=source, index=i, name=name)
        family = _parse_family(item, source=source, index=i, name=name)
        seen.add(name)
        endpoints.append(
            Endpoint(
                name=name,
                url=url,
                headers=headers,
                ws_url=ws_url,
                family=family,
            )
        )
    return BenchConfig(endpoints=tuple(endpoints))


def _parse_headers(
    item: dict, *, source: str, index: int, name: str
) -> tuple[tuple[str, str], ...]:
    headers: list[tuple[str, str]] = []
    raw = item.get("headers")
    if raw is not None:
        if not isinstance(raw, dict):
            raise ConfigError(
                f"{source}: endpoints[{index}] ({name}) headers must be a mapping"
            )
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ConfigError(
                    f"{source}: endpoints[{index}] ({name}) header names and values "
                    "must be strings"
                )
            headers.append((key.strip(), value))
    bearer = item.get("bearer")
    if bearer is not None:
        if not isinstance(bearer, str) or not bearer.strip():
            raise ConfigError(
                f"{source}: endpoints[{index}] ({name}) bearer must be a string"
            )
        headers.append(("Authorization", f"Bearer {bearer.strip()}"))
    return tuple(headers)


def _parse_ws_url(
    item: dict, *, source: str, index: int, name: str
) -> str | None:
    raw_ws = item.get("ws")
    raw_websocket = item.get("websocket")
    if (
        raw_ws is not None
        and raw_websocket is not None
        and str(raw_ws).strip() != str(raw_websocket).strip()
    ):
        raise ConfigError(
            f"{source}: endpoints[{index}] ({name}) has both ws and websocket"
        )
    raw = raw_ws if raw_ws is not None else raw_websocket
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(
            f"{source}: endpoints[{index}] ({name}) ws must be a ws or wss URL"
        )
    ws_url = raw.strip()
    scheme = ws_url.split(":", 1)[0].lower()
    if scheme not in {"ws", "wss"}:
        raise ConfigError(
            f"{source}: endpoints[{index}] ({name}) WS URL must be ws or wss"
        )
    return ws_url


def _parse_family(item: dict, *, source: str, index: int, name: str) -> str:
    from rpcbench.family import normalize_family

    if "family" not in item:
        return normalize_family(None)
    raw = item.get("family")
    if not isinstance(raw, str):
        raise ConfigError(
            f"{source}: endpoints[{index}] ({name}) family must be a name"
        )
    try:
        return normalize_family(raw)
    except ConfigError as exc:
        raise ConfigError(f"{source}: endpoints[{index}] ({name}) {exc}") from exc
