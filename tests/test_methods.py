from __future__ import annotations

import pytest

from rpcbench.methods import MethodError, resolve_method


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
