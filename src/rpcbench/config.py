"""Load named RPC endpoints from YAML or JSON. Localhost is allowed."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str


@dataclass(frozen=True)
class BenchConfig:
    endpoints: tuple[Endpoint, ...]


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
        seen.add(name)
        endpoints.append(Endpoint(name=name, url=url))
    return BenchConfig(endpoints=tuple(endpoints))
