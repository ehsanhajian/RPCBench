from __future__ import annotations

import pytest

from rpcbench.methods import (
    APP_WORKLOADS,
    FAMILY_EVM,
    MIX_PROFILE,
    PRESETS,
    WORKLOADS,
    MethodError,
    _WRITE_PREFIXES,
    canonical_workload,
    family_workload,
    request_units,
    resolve_method,
    resolve_workload,
)


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


def test_mix_profile_has_head_state_call_logs() -> None:
    names = {spec.name for spec in MIX_PROFILE}
    methods = {spec.method for spec in MIX_PROFILE}
    assert names >= {"head", "balance", "call", "logs"}
    assert methods >= {
        "eth_blockNumber",
        "eth_getBalance",
        "eth_call",
        "eth_getLogs",
    }
    logs = next(spec for spec in MIX_PROFILE if spec.name == "logs")
    filt = logs.params[0]
    assert isinstance(filt, dict)
    assert filt["fromBlock"] == "latest"
    assert filt["toBlock"] == "latest"
    assert "address" in filt


def test_resolve_workload_mix() -> None:
    label, steps = resolve_workload(
        profile="mix", method=None, preset=None, params_json=None
    )
    assert label == "mix"
    assert steps == MIX_PROFILE
    assert canonical_workload("mix") == "general"


def test_resolve_workload_general_matches_mix() -> None:
    label, steps = resolve_workload(
        workload="general",
        profile=None,
        method=None,
        preset=None,
        params_json=None,
    )
    assert label == "general"
    assert steps == MIX_PROFILE
    assert request_units(steps) == 6


def test_profile_conflicts_with_method() -> None:
    with pytest.raises(MethodError, match="without --method"):
        resolve_workload(
            profile="mix", method="eth_chainId", preset=None, params_json=None
        )


def test_profile_rejects_params() -> None:
    with pytest.raises(MethodError, match="fixed"):
        resolve_workload(
            profile="mix", method=None, preset=None, params_json="[]"
        )


def test_workload_and_profile_must_agree() -> None:
    with pytest.raises(MethodError, match="not both"):
        resolve_workload(
            profile="mix",
            workload="wallet",
            method=None,
            preset=None,
            params_json=None,
        )
    label, steps = resolve_workload(
        profile="mix",
        workload="general",
        method=None,
        preset=None,
        params_json=None,
    )
    assert label == "general"
    assert steps == MIX_PROFILE


def test_unknown_workload() -> None:
    with pytest.raises(MethodError, match="unknown --profile"):
        resolve_workload(
            profile="nope", method=None, preset=None, params_json=None
        )
    with pytest.raises(MethodError, match="unknown --workload"):
        resolve_workload(
            workload="nope",
            profile=None,
            method=None,
            preset=None,
            params_json=None,
        )


def test_evm_catalogs_are_documented_and_weighted() -> None:
    catalogs = WORKLOADS[FAMILY_EVM]
    assert set(catalogs) == set(APP_WORKLOADS)
    for name in APP_WORKLOADS:
        steps = family_workload(FAMILY_EVM, name)
        assert steps
        assert all(spec.weight >= 1 for spec in steps)
        names = [spec.name for spec in steps]
        assert len(names) == len(set(names))


def test_indexer_has_bounded_logs_wallet_does_not() -> None:
    wallet = {spec.name: spec for spec in family_workload(FAMILY_EVM, "wallet")}
    indexer = {spec.name: spec for spec in family_workload(FAMILY_EVM, "indexer")}
    assert "logs" not in wallet
    assert wallet["balance"].weight > wallet["head"].weight
    assert wallet["call"].weight > wallet["head"].weight
    logs = indexer["logs"]
    filt = logs.params[0]
    assert logs.weight >= indexer["head"].weight
    assert filt["fromBlock"] == "latest"
    assert filt["toBlock"] == "latest"
    assert "address" in filt


def test_trading_and_nft_mixes() -> None:
    trading = {spec.name: spec for spec in family_workload(FAMILY_EVM, "trading")}
    nft = {spec.name: spec for spec in family_workload(FAMILY_EVM, "nft")}
    assert "logs" not in trading
    assert trading["call"].weight >= trading["head"].weight
    assert "logs" in nft
    assert nft["call"].weight > 1
    assert nft["logs"].weight > 1
    filt = nft["logs"].params[0]
    assert filt["fromBlock"] == filt["toBlock"] == "latest"


def test_app_mixes_never_include_tracing_or_privileged() -> None:
    blob = " ".join(
        spec.method
        for family in WORKLOADS.values()
        for steps in family.values()
        for spec in steps
    ).lower()
    for marker in (
        "trace_",
        "debug_",
        "admin_",
        "personal_",
        "miner_",
        "engine_",
        "txpool_",
        "eth_accounts",
        "rpc_modules",
    ):
        assert marker not in blob
    for family in WORKLOADS.values():
        for steps in family.values():
            for spec in steps:
                lower = spec.method.lower()
                assert not any(lower.startswith(p) for p in _WRITE_PREFIXES)


def test_solana_family_is_not_an_evm_fallback() -> None:
    with pytest.raises(MethodError, match="evm-only"):
        family_workload("solana", "wallet")

