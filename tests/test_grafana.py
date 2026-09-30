from __future__ import annotations

import json
import re
from pathlib import Path

from rpcbench.prometheus import DOCUMENTED_METRICS

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "grafana" / "dashboards" / "rpcbench.json"
PROVISIONING = ROOT / "grafana" / "provisioning" / "dashboards.yml"
DOCS = ROOT / "docs"
_METRIC_RE = re.compile(r"\brpcbench_[a-z0-9_]+")


def test_grafana_dashboard_is_valid_json() -> None:
    data = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert data["uid"] == "rpcbench"
    assert data["title"] == "RPCBench"
    assert data["panels"], "dashboard needs panels"
    assert any(p.get("title") == "P95 latency by provider" for p in data["panels"])
    assert any(p.get("title") == "Error rate by provider" for p in data["panels"])


def test_grafana_dashboard_metric_names_match_docs() -> None:
    blob = DASHBOARD.read_text(encoding="utf-8")
    used = set(_METRIC_RE.findall(blob))
    assert used, "dashboard must reference rpcbench_* metrics"
    unknown = used - DOCUMENTED_METRICS
    assert unknown == set(), f"dashboard uses undocumented metrics: {sorted(unknown)}"
    # Core AT panels: latency + errors must appear.
    assert "rpcbench_latency_ms" in used
    assert "rpcbench_error_rate" in used
    docs = (DOCS / "PROMETHEUS.md").read_text(encoding="utf-8")
    for name in (
        "rpcbench_latency_ms",
        "rpcbench_error_rate",
        "rpcbench_rps",
        "rpcbench_reliability_score",
        "rpcbench_samples",
        "rpcbench_info",
    ):
        assert f"`{name}`" in docs, f"{name} missing from PROMETHEUS.md"
        assert name in used


def test_grafana_provisioning_points_at_dashboards() -> None:
    text = PROVISIONING.read_text(encoding="utf-8")
    assert "path: /var/lib/grafana/dashboards/rpcbench" in text
    assert "folder: RPCBench" in text


def test_grafana_docs_exist() -> None:
    text = (DOCS / "GRAFANA.md").read_text(encoding="utf-8")
    assert "grafana/dashboards/rpcbench.json" in text
    assert "PROMETHEUS.md" in text
