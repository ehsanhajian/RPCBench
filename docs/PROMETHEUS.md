# Prometheus metrics

RPCBench can dump a **Prometheus textfile** after a compare. This is a benchmark exporter, not a long-running `/metrics` HTTP server.

```bash
rpcbench compare --endpoints endpoints.yaml --prometheus > metrics.prom
rpcbench compare --endpoints endpoints.yaml --prometheus -o metrics.prom
rpcbench compare --endpoints endpoints.yaml --out-dir out   # also writes out/metrics.prom
```

Point [node_exporter](https://github.com/prometheus/node_exporter) textfile collector at the file, or scrape it with a sidecar that serves the text.

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

- Values are **this run only**. Re-run to refresh the file.
- Missing successes omit latency / rps series for that provider; error rate stays present.
- Mutually exclusive on stdout with `--json` / `--md` / `--csv`.

## Grafana

Import [`grafana/dashboards/rpcbench.json`](../grafana/dashboards/rpcbench.json) (Cloud / UI) or file-provision with [`grafana/provisioning/dashboards.yml`](../grafana/provisioning/dashboards.yml). Steps: [GRAFANA.md](GRAFANA.md).
