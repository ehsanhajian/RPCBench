# Grafana dashboard

Dashboard for [Prometheus textfile metrics](PROMETHEUS.md): P95, error rate, RPS, reliability, samples, and latency histogram — filtered by provider / method / family / chain.

**Freshness / head lag** are not Prometheus series (use JSON/HTML reports).

| File | Role |
| --- | --- |
| [`grafana/dashboards/rpcbench.json`](../grafana/dashboards/rpcbench.json) | Dashboard JSON (UID `rpcbench`) |
| [`grafana/provisioning/dashboards.yml`](../grafana/provisioning/dashboards.yml) | File provider → folder **RPCBench** |

## Prerequisites

1. Dump and scrape metrics so Prometheus has `rpcbench_*` — follow [PROMETHEUS.md](PROMETHEUS.md).
2. In Prometheus (http://localhost:9090), confirm:

   ```promql
   rpcbench_latency_ms{quantile="0.95"}
   rpcbench_error_rate
   ```

   One series per provider = healthy scrape. Fix path / target before opening Grafana.

## Import (Cloud or any Grafana UI)

1. Scraping `metrics.prom` works (local Prometheus, Grafana Cloud, or Agent).
2. **Dashboards → New → Import** → upload `grafana/dashboards/rpcbench.json`.
3. Select your Prometheus datasource.
4. Filter with the top variables: **Provider**, **Method**, **Family**, **Chain**.

Expected: **P95 latency by provider** and **Error rate by provider** show each endpoint from the last compare.

## Local stack (file provisioning)

With metrics served (see [PROMETHEUS.md](PROMETHEUS.md)) and Prometheus on `:9090`:

```bash
docker run --rm -p 3000:3000 \
  -v "$PWD/grafana/dashboards:/var/lib/grafana/dashboards/rpcbench:ro" \
  -v "$PWD/grafana/provisioning/dashboards.yml:/etc/grafana/provisioning/dashboards/rpcbench.yml:ro" \
  grafana/grafana:latest
```

1. http://localhost:3000 — default `admin` / `admin`.
2. **Connections → Data sources → Add Prometheus**.
3. URL: `http://host.docker.internal:9090` (Docker Desktop) or the Prometheus service name on a shared network.
4. **Save & test**, then open **Dashboards → RPCBench**.
5. Set the **Datasource** variable to that Prometheus.

### End-to-end checklist

```bash
# 1. Write metrics
mkdir -p /tmp/rpcbench
rpcbench compare --endpoints endpoints.ci.yaml --samples 2 --warmup 0 \
  --prometheus -o /tmp/rpcbench/metrics.prom

# 2. Serve file (terminal A)
python3 -m http.server 9100 --directory /tmp/rpcbench

# 3. Prometheus scrape → host.docker.internal:9100/metrics.prom (terminal B)
# 4. Grafana as above (terminal C)
# 5. Re-run step 1; wait for the next scrape; panels update
```

## Grafana Cloud

1. Ship the textfile (Agent / Alloy scrapes the same HTTP path or node_exporter).
2. **Import** → `grafana/dashboards/rpcbench.json`.
3. Bind the Cloud Prometheus datasource.

## Panels ↔ metrics

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

With `--shape`, also query `rpcbench_shape_rps` / `rpcbench_shape_p95_ms` / `rpcbench_shape_error_rate` (labels `offset_s`, `shape`) or open the HTML sparklines. See [METHODOLOGY.md § Load shapes](METHODOLOGY.md#load-shapes).

Empty panels usually mean a bad scrape target, a stale file, or a datasource URL the Grafana container cannot reach.
