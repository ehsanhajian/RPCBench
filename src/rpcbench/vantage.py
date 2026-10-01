"""Where a run was measured from. One compare = one vantage; not browser RUM."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VantageInfo:
    """Client-side measurement location. Not the RPC provider's region."""

    label: str
    region: str | None = None
    city: str | None = None
    asn: str | None = None
    hostname: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "region": self.region,
            "city": self.city,
            "asn": self.asn,
            "hostname": self.hostname,
        }


def resolve_vantage() -> VantageInfo:
    """Label from ``RPCBENCH_VANTAGE`` or hostname; extras from env when set.

    Optional env (no CLI flags): ``RPCBENCH_REGION``, ``RPCBENCH_CITY``,
    ``RPCBENCH_ASN``. Hostname is always recorded when resolvable.
    """
    host = _hostname()
    label = os.environ.get("RPCBENCH_VANTAGE", "").strip() or host or "local"
    return VantageInfo(
        label=label,
        region=_env("RPCBENCH_REGION"),
        city=_env("RPCBENCH_CITY"),
        asn=_env("RPCBENCH_ASN"),
        hostname=host,
    )


def vantage_label() -> str:
    """Back-compat: the short label used on Cite lines and CSV."""
    return resolve_vantage().label


def format_vantage_bits(info: VantageInfo | dict[str, Any] | None) -> str:
    """Compact extras for Cite / HTML (empty when only the label is known)."""
    data = _as_mapping(info)
    if not data:
        return ""
    bits: list[str] = []
    for key, prefix in (
        ("region", "region"),
        ("city", "city"),
        ("asn", "asn"),
        ("hostname", "host"),
    ):
        value = data.get(key)
        if value:
            bits.append(f"{prefix}={value}")
    return "  ".join(bits)


def _as_mapping(info: VantageInfo | dict[str, Any] | None) -> dict[str, Any]:
    if info is None:
        return {}
    if isinstance(info, VantageInfo):
        return info.as_dict()
    if isinstance(info, dict):
        return info
    return {}


def _env(name: str) -> str | None:
    text = os.environ.get(name, "").strip()
    return text or None


def _hostname() -> str | None:
    try:
        return socket.gethostname() or None
    except OSError:
        return None
