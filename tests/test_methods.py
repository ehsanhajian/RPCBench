from __future__ import annotations

import pytest

from rpcbench.methods import PRESETS, MethodError, _WRITE_PREFIXES, resolve_method


def test_default_is_eth_blockNumber() -> None:
    method, params = resolve_method(method=None, preset=None, params_json=None)
    assert method == "eth_blockNumber"
    assert params == []


def test_preset_balance() -> None:
    method, params = resolve_method(method=None, preset="balance", params_json=None)
    assert method == "eth_getBalance"
    assert params[0].startswith("0x") and params[1] == "latest"


def test_preset_is_case_insensitive() -> None:
    method, _ = resolve_method(method=None, preset="chainid", params_json=None)
    assert method == "eth_chainId"


def test_preset_and_method_conflict() -> None:
    with pytest.raises(MethodError, match="either --preset or --method"):
        resolve_method(method="eth_chainId", preset="head", params_json=None)


def test_rejects_write_methods() -> None:
    with pytest.raises(MethodError, match="write method"):
        resolve_method(method="eth_sendTransaction", preset=None, params_json=None)
    with pytest.raises(MethodError, match="write method"):
        resolve_method(method="personal_sendTransaction", preset=None, params_json=None)


def test_allow_writes_permits_send() -> None:
    method, params = resolve_method(
        method="eth_sendRawTransaction",
        preset=None,
        params_json=None,
        allow_writes=True,
    )
    assert method == "eth_sendRawTransaction"
    assert params == []


def test_presets_are_read_only() -> None:
    for name, (method, _params) in PRESETS.items():
        lower = method.lower()
        assert not any(lower.startswith(p) for p in _WRITE_PREFIXES), name
        resolve_method(method=None, preset=name, params_json=None, allow_writes=False)


def test_params_must_be_json_array() -> None:
    with pytest.raises(MethodError, match="JSON array"):
        resolve_method(method="eth_blockNumber", preset=None, params_json='{"x":1}')
    method, params = resolve_method(
        method="eth_getBalance",
        preset=None,
        params_json='["0x0","latest"]',
    )
    assert method == "eth_getBalance"
    assert params == ["0x0", "latest"]
