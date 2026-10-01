# Prometheus metrics

RPCBench writes a **Prometheus textfile** after a compare. It is not a long-running `/metrics` server — re-run the CLI whenever you want fresh values.

Grafana dashboards: [GRAFANA.md](GRAFANA.md).

## Dump a file

```bash
rpcbench compare --endpoints endpoints.yaml --prometheus              # stdout
rpcbench compare --endpoints endpoints.yaml --prometheus -o metrics.prom
rpcbench compare --endpoints endpoints.yaml --out-dir out              # includes metrics.prom
```

Paths ending in `.prom` / `.prometheus` imply Prometheus output. `--prometheus` cannot share stdout with `--json` / `--md` / `--csv`.

Quick check (no Prometheus needed):

```bash
rpcbench compare --endpoints endpoints.ci.yaml --samples 2 --warmup 0 \
  --prometheus -o /tmp/rpcbench/metrics.prom
grep rpcbench_error_rate /tmp/rpcbench/metrics.prom
```

You should see `rpcbench_latency_ms`, `rpcbench_error_rate`, and `provider="…"` labels.

## Scrape

Prometheus scrapes HTTP. Pick one:

### A. Static file over HTTP (local / demo)

```bash
mkdir -p /tmp/rpcbench
rpcbench compare --endpoints endpoints.yaml --prometheus -o /tmp/rpcbench/metrics.prom
python3 -m http.server 9100 --directory /tmp/rpcbench   # /metrics.prom
```

`prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: rpcbench
    metrics_path: /metrics.prom
    static_configs:
      - targets: ["host.docker.internal:9100"]   # Docker → host
      # - targets: ["127.0.0.1:9100"]            # same host
```

```bash
docker run --rm -p 9090:9090 \
  -v "$PWD/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  prom/prometheus:latest
```

Graph: `rpcbench_latency_ms{quantile="0.95"}` and `rpcbench_error_rate` — one series per provider. Overwrite the file to refresh; the next scrape picks it up.

### B. node_exporter textfile collector

```bash
rpcbench compare --endpoints endpoints.yaml --prometheus \
  -o /var/lib/node_exporter/textfile_collector/rpcbench.prom
```

Run node_exporter with `--collector.textfile.directory=…`. Series keep the usual `job` / `instance` labels plus RPCBench’s `provider` / `method` / `family` / `chain`.

### C. Scheduled refresh

Values are **this run only**. Cron or CI should overwrite the file:

```bash
*/15 * * * * cd /path/to/work && rpcbench compare --endpoints endpoints.yaml \
  --budget short --prometheus -o /var/lib/node_exporter/textfile_collector/rpcbench.prom
```

Or use the [GitHub Action](CI.md) `--out-dir` and scrape `metrics.prom` from artifacts.

## Metrics

| Metric | Type | Meaning |
| --- | --- | --- |
| `rpcbench_info` | gauge | Always `1`; run labels (`family`, `method`, `sample_budget`, `version`) |
| `rpcbench_latency_ms` | gauge | Percentiles / mean (`quantile` = `0.5`, `0.95`, `0.99`, `mean`) |
| `rpcbench_error_rate` | gauge | Failed / attempted (`0`–`1`) |
| `rpcbench_rps` | gauge | `1000 / mean_ms` for successes |
| `rpcbench_reliability_score` | gauge | This-run reliability `0`–`100` (not an SLA) |
| `rpcbench_samples` | gauge | Timed counts (`result` = `ok` \| `fail`) |
| `rpcbench_latency_histogram` | histogram | Buckets `le` = `50`, `100`, `250`, `1000`, `+Inf` ms |
| `rpcbench_shape_rps` | gauge | `--shape` window RPS (`offset_s`, `shape`) |
| `rpcbench_shape_p95_ms` | gauge | `--shape` window P95 |
| `rpcbench_shape_error_rate` | gauge | `--shape` window error rate |
| `rpcbench_shape_target_rps` | gauge | Target start rate from the curve |

Histogram exports as `_bucket` / `_count` / `_sum`.

## Labels

| Label | Where |
| --- | --- |
| `provider` | Endpoint name |
| `method` | Primary method |
| `family` | `evm`, `solana`, … |
| `chain` | When a chain id was bound |
| `quantile` | On `rpcbench_latency_ms` |
| `result` | On `rpcbench_samples` |
| `le` | On histogram buckets |
| `offset_s` / `shape` | On `rpcbench_shape_*` |

## Notes

- No successes → latency / rps series omitted for that provider; error rate remains.
- **Freshness / head lag** are JSON/HTML only — not in this exporter.
