from __future__ import annotations

from pathlib import Path

import pytest

from rpcbench.config import ConfigError, load_endpoints, parse_endpoints
from rpcbench.urls import display_url


def test_parse_two_endpoints() -> None:
    cfg = parse_endpoints(
        {
            "endpoints": [
                {"name": "local", "url": "http://127.0.0.1:8545"},
                {"name": "public", "url": "https://example.invalid"},
            ]
        }
    )
    assert [e.name for e in cfg.endpoints] == ["local", "public"]
    assert cfg.endpoints[0].url.startswith("http://127.0.0.1")


def test_localhost_is_allowed() -> None:
    cfg = parse_endpoints(
        {"endpoints": [{"name": "local", "url": "http://localhost:8545"}]}
    )
    assert cfg.endpoints[0].url == "http://localhost:8545"


def test_rejects_empty_and_bad_scheme() -> None:
    with pytest.raises(ConfigError, match="non-empty"):
        parse_endpoints({"endpoints": []})
    with pytest.raises(ConfigError, match="http or https"):
        parse_endpoints({"endpoints": [{"name": "x", "url": "ws://example"}]})
    with pytest.raises(ConfigError, match="duplicate"):
        parse_endpoints(
            {
                "endpoints": [
                    {"name": "a", "url": "http://127.0.0.1:1"},
                    {"name": "a", "url": "http://127.0.0.1:2"},
                ]
            }
        )


def test_load_yaml_and_json(tmp_path: Path) -> None:
    yml = tmp_path / "e.yaml"
    yml.write_text(
        "endpoints:\n  - name: a\n    url: http://127.0.0.1:8545\n",
        encoding="utf-8",
    )
    js = tmp_path / "e.json"
    js.write_text(
        '{"endpoints":[{"name":"a","url":"http://127.0.0.1:8545"}]}',
        encoding="utf-8",
    )
    assert load_endpoints(yml).endpoints[0].name == "a"
    assert load_endpoints(js).endpoints[0].name == "a"


def test_ci_endpoints_are_public_https() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = load_endpoints(root / "endpoints.ci.yaml")
    assert len(cfg.endpoints) >= 2
    for endpoint in cfg.endpoints:
        assert endpoint.url.startswith("https://")
        lowered = endpoint.url.lower()
        assert "127.0.0.1" not in lowered
        assert "localhost" not in lowered


def test_display_url_redacts_secrets() -> None:
    assert "***@" in display_url("https://user:secret@rpc.example/path")
    assert "[redacted]" in display_url("https://rpc.example/?apiKey=abc")
    assert "rpc.example" in display_url("https://rpc.example/v1")
