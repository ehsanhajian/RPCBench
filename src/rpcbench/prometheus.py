"""Prometheus textfile exposition for a finished run. Not a scrape server."""

from __future__ import annotations

from typing import Any

from rpcbench.reliability import assess as assess_reliability
from rpcbench.report import DEFAULT_RANK_BY, DEFAULT_SIMILAR_BAND, normalize_rank_by
from rpcbench.run import HISTOGRAM_EDGES_MS, HISTOGRAM_LABELS, RunResult

# Cumulative histogram upper bounds (ms). Last bucket is +Inf.
_BUCKET_LE = tuple(str(int(edge)) if edge == int(edge) else str(edge) for edge in HISTOGRAM_EDGES_MS) + (
    "+Inf",
)


def format_prometheus(
    result: RunResult,
    *,
    rank_by: str = DEFAULT_RANK_BY,
    similar_band: float = DEFAULT_SIMILAR_BAND,
) -> str:
    """Prometheus text format 0.0.4 for this run (gauges + latency histogram).

    Labels: ``provider``, ``method``, ``family``, and ``chain`` when chain id is known.
    Dump with ``--prometheus`` (stdout / file / ``--out-dir``). Not a long-running
    ``/metrics`` server — use node_exporter textfile collector or ``curl`` a file.
    """
    del similar_band  # reserved for parity with other formatters
    normalize_rank_by(rank_by)
    method = result.method or "unknown"
    family = result.family or "unknown"
    chain = _chain_label(result)
    lines: list[str] = [
        "# HELP rpcbench_info RPCBench run watermark (always 1).",
        "# TYPE rpcbench_info gauge",
        _sample(
            "rpcbench_info",
            1,
            {
                "version": _safe(getattr(result, "git_sha", None) or ""),
                "family": family,
                "method": method,
                "sample_budget": _safe(result.sample_budget or ""),
            },
        ),
        "# HELP rpcbench_latency_ms Latency percentile for successful samples (milliseconds).",
        "# TYPE rpcbench_latency_ms gauge",
    ]
    for outcome in result.outcomes:
        labels = _provider_labels(outcome.endpoint.name, method, family, chain)
        stats = outcome.stats
        for quantile, value in (
            ("0.5", stats.p50_ms),
            ("0.95", stats.p95_ms),
            ("0.99", stats.p99_ms),
            ("mean", stats.mean_ms),
        ):
            if value is None:
                continue
            lines.append(
                _sample("rpcbench_latency_ms", value, {**labels, "quantile": quantile})
            )

    lines.extend(
        [
            "# HELP rpcbench_error_rate Fraction of failed samples in this run (0–1).",
            "# TYPE rpcbench_error_rate gauge",
        ]
    )
    for outcome in result.outcomes:
        labels = _provider_labels(outcome.endpoint.name, method, family, chain)
        rate = outcome.stats.error_rate
        if rate is None:
            rate = 1.0 if outcome.stats.n_ok == 0 else 0.0
        lines.append(_sample("rpcbench_error_rate", rate, labels))

    lines.extend(
        [
            "# HELP rpcbench_rps Estimated successful requests per second (1000 / mean_ms).",
            "# TYPE rpcbench_rps gauge",
        ]
    )
    for outcome in result.outcomes:
        labels = _provider_labels(outcome.endpoint.name, method, family, chain)
        mean = outcome.stats.mean_ms
        if mean and mean > 0:
            lines.append(_sample("rpcbench_rps", 1000.0 / mean, labels))

    lines.extend(
        [
            "# HELP rpcbench_reliability_score This-run reliability score (0–100). Not an SLA.",
            "# TYPE rpcbench_reliability_score gauge",
        ]
    )
    for outcome in result.outcomes:
        labels = _provider_labels(outcome.endpoint.name, method, family, chain)
        score = assess_reliability(outcome).score
        lines.append(_sample("rpcbench_reliability_score", score, labels))

    lines.extend(
        [
            "# HELP rpcbench_samples Number of timed samples in this run.",
            "# TYPE rpcbench_samples gauge",
        ]
    )
    for outcome in result.outcomes:
        labels = _provider_labels(outcome.endpoint.name, method, family, chain)
        lines.append(
            _sample("rpcbench_samples", outcome.stats.n_ok, {**labels, "result": "ok"})
        )
        lines.append(
            _sample(
                "rpcbench_samples", outcome.stats.n_fail, {**labels, "result": "fail"}
            )
        )

    lines.extend(
        [
            "# HELP rpcbench_latency_histogram Latency sample histogram (milliseconds).",
            "# TYPE rpcbench_latency_histogram histogram",
        ]
    )
    for outcome in result.outcomes:
        labels = _provider_labels(outcome.endpoint.name, method, family, chain)
        hist = dict(outcome.stats.histogram or ())
        cumulative = 0
        for label, le in zip(HISTOGRAM_LABELS, _BUCKET_LE, strict=True):
            cumulative += int(hist.get(label, 0))
            lines.append(
                _sample(
                    "rpcbench_latency_histogram_bucket",
                    cumulative,
                    {**labels, "le": le},
                )
            )
        total = outcome.stats.n_ok
        lines.append(_sample("rpcbench_latency_histogram_count", total, labels))
        if outcome.stats.mean_ms is not None and total:
            lines.append(
                _sample(
                    "rpcbench_latency_histogram_sum",
                    outcome.stats.mean_ms * total,
                    labels,
                )
            )
        else:
            lines.append(_sample("rpcbench_latency_histogram_sum", 0, labels))

    lines.append("")
    return "\n".join(lines)


def validate_prometheus_text(blob: str) -> list[str]:
    """Return human problems if ``blob`` is not valid Prometheus text 0.0.4."""
    problems: list[str] = []
    if not blob.endswith("\n"):
        problems.append("missing trailing newline")
    for i, line in enumerate(blob.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        if "{" in line:
            name, rest = line.split("{", 1)
            if "}" not in rest:
                problems.append(f"line {i}: unclosed labels")
                continue
            _labels, _, value = rest.partition("}")
            value = value.strip()
            if not _is_metric_name(name):
                problems.append(f"line {i}: bad metric name {name!r}")
            token = value.split()[0] if value else ""
            if not _is_float(token):
                problems.append(f"line {i}: bad value {value!r}")
        else:
            parts = line.split()
            if len(parts) < 2 or not _is_metric_name(parts[0]) or not _is_float(parts[1]):
                problems.append(f"line {i}: expected name value, got {line!r}")
    for required in (
        "rpcbench_latency_ms",
        "rpcbench_error_rate",
        "rpcbench_reliability_score",
        "rpcbench_latency_histogram_bucket",
    ):
        if required not in blob:
            problems.append(f"missing metric {required}")
    return problems


def _provider_labels(
    provider: str, method: str, family: str, chain: str | None
) -> dict[str, str]:
    labels = {
        "provider": provider,
        "method": method,
        "family": family,
    }
    if chain:
        labels["chain"] = chain
    return labels


def _chain_label(result: RunResult) -> str | None:
    payload = result.payload
    if payload is not None and payload.chain_id is not None:
        return str(payload.chain_id)
    return None


def _sample(name: str, value: float | int, labels: dict[str, str]) -> str:
    body = ",".join(f'{key}="{_escape(val)}"' for key, val in labels.items())
    return f"{name}{{{body}}} {_fmt(value)}"


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def _safe(text: Any) -> str:
    return str(text or "")


def _fmt(value: float | int) -> str:
    if isinstance(value, int) or (isinstance(value, float) and value == int(value)):
        return str(int(value))
    return f"{float(value):.6g}"


def _is_metric_name(name: str) -> bool:
    if not name or name[0].isdigit():
        return False
    return all(ch.isalnum() or ch == "_" or ch == ":" for ch in name)


def _is_float(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False
