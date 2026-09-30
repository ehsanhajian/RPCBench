# Grafana dashboard

Provisionable dashboard for [Prometheus textfile metrics](PROMETHEUS.md) (`--prometheus` / `metrics.prom`). Graphs P95 latency, error rate, RPS, reliability, samples, and latency histogram buckets by provider / method / family / chain.

**Freshness / head lag** stays in the JSON and HTML reports — it is not exported as a Prometheus series today.

## Import (Grafana Cloud or UI)

1. Dump metrics and scrape them (node_exporter textfile collector, or any sidecar that serves `metrics.prom`).
2. In Grafana: **Dashboards → New → Import**.
3. Upload [`grafana/dashboards/rpcbench.json`](../grafana/dashboards/rpcbench.json) (or paste the JSON).
4. Pick your Prometheus datasource when prompted.

Dashboard UID: `rpcbench`. Template variables: datasource, provider, method, family, chain.

## Local Grafana (file provisioning)

```bash
# Example docker run — adjust paths to your checkout and Prometheus scrape setup.
docker run --rm -p 3000:3000 \
  -v "$PWD/grafana/dashboards:/var/lib/grafana/dashboards/rpcbench:ro" \
  -v "$PWD/grafana/provisioning/dashboards.yml:/etc/grafana/provisioning/dashboards/rpcbench.yml:ro" \
  grafana/grafana:latest
```

Open http://localhost:3000 (default `admin` / `admin`). The dashboard appears under folder **RPCBench**.

## Metric names

Panels query only names documented in [PROMETHEUS.md](PROMETHEUS.md):

| Panel | Metric |
| --- | --- |
| Exporter present | `rpcbench_info` |
| P95 latency | `rpcbench_latency_ms{quantile="0.95"}` |
| Error rate | `rpcbench_error_rate` |
| RPS | `rpcbench_rps` |
| Reliability | `rpcbench_reliability_score` |
| Samples | `rpcbench_samples` |
| Latency table | `rpcbench_latency_ms` |
| Histogram | `rpcbench_latency_histogram_bucket` |

Re-run `rpcbench compare … --prometheus` (or `--out-dir`) on a schedule so Prometheus keeps fresh scrapes; the textfile is this-run only.
