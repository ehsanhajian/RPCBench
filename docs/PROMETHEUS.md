# Prometheus metrics

RPCBench dumps a **Prometheus textfile** after a compare. It is a benchmark exporter, not a long-running `/metrics` HTTP server. Re-run the CLI whenever you want fresh values; scrapers only see what is in the file.

## Dump a textfile

```bash
# stdout
rpcbench compare --endpoints endpoints.yaml --prometheus

# file
rpcbench compare --endpoints endpoints.yaml --prometheus -o metrics.prom
# or any path ending in .prom / .prometheus
rpcbench compare --endpoints endpoints.yaml -o /tmp/rpcbench/metrics.prom

# CI / Action out-dir also writes metrics.prom next to the HTML/JSON reports
rpcbench compare --endpoints endpoints.yaml --out-dir out
ls out/metrics.prom
```

Quick sanity check (no Prometheus required):

```bash
rpcbench compare --endpoints endpoints.ci.yaml --samples 2 --warmup 0 \
  --prometheus -o /tmp/rpcbench/metrics.prom
head -40 /tmp/rpcbench/metrics.prom
grep rpcbench_error_rate /tmp/rpcbench/metrics.prom
```

You should see `rpcbench_latency_ms`, `rpcbench_error_rate`, and `provider="…"` labels. `--prometheus` is mutually exclusive on stdout with `--json` / `--md` / `--csv`.

## Scrape the file

Prometheus must scrape HTTP. Pick one of these:

### A. Static file over HTTP (local / demo)

```bash
mkdir -p /tmp/rpcbench
rpcbench compare --endpoints endpoints.yaml --prometheus -o /tmp/rpcbench/metrics.prom

# Serves http://127.0.0.1:9100/metrics.prom
python3 -m http.server 9100 --directory /tmp/rpcbench
```

`prometheus.yml`:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: rpcbench
    metrics_path: /metrics.prom
    static_configs:
      # From a Prometheus container on Docker Desktop / Linux with host gateway:
      - targets: ["host.docker.internal:9100"]
      # Or scrape from the same host as Prometheus:
      # - targets: ["127.0.0.1:9100"]
```

```bash
docker run --rm -p 9090:9090 \
  -v "$PWD/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  prom/prometheus:latest
```

Open http://localhost:9090 → **Graph** → run:

```promql
rpcbench_latency_ms{quantile="0.95"}
rpcbench_error_rate
```

You should get one series per provider. Refresh the file by re-running `rpcbench compare … -o /tmp/rpcbench/metrics.prom`; the next scrape picks it up.

### B. node_exporter textfile collector

```bash
# Write into the textfile directory node_exporter watches
rpcbench compare --endpoints endpoints.yaml --prometheus \
  -o /var/lib/node_exporter/textfile_collector/rpcbench.prom
```

Run node_exporter with `--collector.textfile.directory=/var/lib/node_exporter/textfile_collector`. Prometheus scrapes node_exporter as usual; series appear with the usual `job` / `instance` labels plus RPCBench’s `provider` / `method` / `family` / `chain`.

### C. Scheduled refresh

Textfile values are **this run only**. Cron or CI should overwrite the file on a cadence:

```bash
*/15 * * * * cd /path/to/work && rpcbench compare --endpoints endpoints.yaml \
  --budget short --prometheus -o /var/lib/node_exporter/textfile_collector/rpcbench.prom
```

Or use the [GitHub Action](CI.md) with `--out-dir` and publish / scrape `metrics.prom` from the artifact pipeline.

## Metric names

| Metric | Type | Meaning |
| --- | --- | --- |
| `rpcbench_info` | gauge | Always `1`; run labels (`family`, `method`, `sample_budget`, `version` git sha when known) |
| `rpcbench_latency_ms` | gauge | Latency percentiles / mean (`quantile` = `0.5`, `0.95`, `0.99`, `mean`) |
| `rpcbench_error_rate` | gauge | Failed / attempted samples (`0`–`1`) |
| `rpcbench_rps` | gauge | `1000 / mean_ms` for successful samples |
| `rpcbench_reliability_score` | gauge | This-run reliability `0`–`100` (not an SLA) |
| `rpcbench_samples` | gauge | Timed sample counts (`result` = `ok` \| `fail`) |
| `rpcbench_latency_histogram` | histogram | Same bucket edges as the CLI (`le` = `50`, `100`, `250`, `1000`, `+Inf` ms) |

Histogram series in the textfile are `rpcbench_latency_histogram_bucket`, `_count`, and `_sum`.

## Labels

| Label | When |
| --- | --- |
| `provider` | Endpoint name from the config |
| `method` | Primary method for the run |
| `family` | Benchmark family (`evm`, `solana`, …) |
| `chain` | Present when the run bound a chain id (payload / `eth_chainId`) |
| `quantile` | On `rpcbench_latency_ms` only |
| `result` | On `rpcbench_samples` only |
| `le` | On histogram buckets |

## Notes

- Missing successes omit latency / rps series for that provider; error rate stays present.
- **Freshness / head lag** is in JSON and HTML reports only — not in this exporter.
- Grafana dashboards: [GRAFANA.md](GRAFANA.md).
