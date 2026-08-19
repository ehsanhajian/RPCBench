"""RPCBench-only kill switch and hard request cap. Not Nodeprobe."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MAX_REQUESTS = 10_000
DISABLE_ENV = "RPCBENCH_DISABLED"
DISABLE_FILE_ENV = "RPCBENCH_DISABLE_FILE"
MAX_REQUESTS_ENV = "RPCBENCH_MAX_REQUESTS"


class SafetyError(RuntimeError):
    pass


def disable_path() -> Path:
    extra = os.environ.get(DISABLE_FILE_ENV, "").strip()
    if extra:
        return Path(extra).expanduser()
    return Path.home() / ".config" / "rpcbench" / "DISABLED"


def kill_switch_reason() -> str | None:
    flag = os.environ.get(DISABLE_ENV, "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return f"{DISABLE_ENV} is set"
    path = disable_path()
    if path.is_file():
        return f"disable file {path}"
    return None


def max_requests() -> int:
    raw = os.environ.get(MAX_REQUESTS_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_REQUESTS
    try:
        value = int(raw)
    except ValueError as exc:
        raise SafetyError(f"{MAX_REQUESTS_ENV} must be an integer") from exc
    if value < 1:
        raise SafetyError(f"{MAX_REQUESTS_ENV} must be >= 1")
    return value


def check_budget(budget: int) -> None:
    cap = max_requests()
    if budget > cap:
        raise SafetyError(
            f"--budget {budget} exceeds hard cap {cap} "
            f"(set {MAX_REQUESTS_ENV} to raise it)"
        )
