from __future__ import annotations

from pathlib import Path

import pytest

from rpcbench.safety import SafetyError, check_budget, kill_switch_reason, max_requests


def test_kill_switch_env(monkeypatch) -> None:
    assert kill_switch_reason() is None
    monkeypatch.setenv("RPCBENCH_DISABLED", "true")
    assert "RPCBENCH_DISABLED" in (kill_switch_reason() or "")


def test_kill_switch_file(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "DISABLED"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("RPCBENCH_DISABLE_FILE", str(path))
    assert "disable file" in (kill_switch_reason() or "")


def test_max_requests_cap(monkeypatch) -> None:
    assert max_requests() == 10_000
    monkeypatch.setenv("RPCBENCH_MAX_REQUESTS", "12")
    check_budget(12)
    with pytest.raises(SafetyError, match="hard cap"):
        check_budget(13)
