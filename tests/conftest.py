from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_rpcbench_safety(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RPCBENCH_DISABLED", raising=False)
    monkeypatch.delenv("RPCBENCH_MAX_REQUESTS", raising=False)
    monkeypatch.setenv("RPCBENCH_DISABLE_FILE", str(tmp_path / "rpcbench-not-disabled"))
