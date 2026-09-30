# Grafana dashboard

Provisionable dashboard for [Prometheus textfile metrics](PROMETHEUS.md). Panels: P95 latency, error rate, RPS, reliability, samples, and latency histogram buckets, filtered by provider / method / family / chain.

**Freshness / head lag** is not a Prometheus series today (use JSON/HTML reports).

| File | Role |
| --- | --- |
| [`grafana/dashboards/rpcbench.json`](../grafana/dashboards/rpcbench.json) | Dashboard JSON (UID `rpcbench`) |
| [`grafana/provisioning/dashboards.yml`](../grafana/provisioning/dashboards.yml) | File provider → folder **RPCBench** |

## Prerequisites

1. Dump and scrape metrics so Prometheus has `rpcbench_*` series — follow [PROMETHEUS.md](PROMETHEUS.md) (HTTP file server, node_exporter textfile, or CI artifact).
2. Confirm in Prometheus UI (http://localhost:9090):

   ```promql
   rpcbench_latency_ms{quantile="0.95"}
   rpcbench_error_rate
   ```

   One series per provider means the exporter is healthy. Fix scrape/path issues before opening Grafana.

## Import (Grafana Cloud or any Grafana UI)

1. Ensure your Prometheus (or Grafana Cloud Prometheus / Agent) scrapes `metrics.prom`.
2. **Dashboards → New → Import**.
3. Upload `grafana/dashboards/rpcbench.json` (or paste the JSON).
4. When prompted, select your Prometheus datasource.
5. Open the dashboard. Use the top variables (**Provider**, **Method**, **Family**, **Chain**) to filter.

Expected: **P95 latency by provider** and **Error rate by provider** show each endpoint from your last compare.

## Local stack (file provisioning)

From the repo root, with metrics already being served (see [PROMETHEUS.md](PROMETHEUS.md) § scrape) and Prometheus on port 9090:

```bash
docker run --rm -p 3000:3000 \
  -v "$PWD/grafana/dashboards:/var/lib/grafana/dashboards/rpcbench:ro" \
  -v "$PWD/grafana/provisioning/dashboards.yml:/etc/grafana/provisioning/dashboards/rpcbench.yml:ro" \
  grafana/grafana:latest
```

1. Open http://localhost:3000 (default `admin` / `admin`).
2. **Connections → Data sources → Add Prometheus**.
3. URL:
   - Docker Desktop / host gateway: `http://host.docker.internal:9090`
   - Prometheus on the same Docker network: use that service name / IP
4. **Save & test** — should report green.
5. Open **Dashboards → RPCBench → RPCBench** (provisioned), or import the JSON manually if the folder is empty.
6. Set the **Datasource** variable to the Prometheus you just added.

### End-to-end checklist

```bash
# 1. Write metrics
mkdir -p /tmp/rpcbench
rpcbench compare --endpoints endpoints.ci.yaml --samples 2 --warmup 0 \
  --prometheus -o /tmp/rpcbench/metrics.prom

# 2. Serve file (terminal A)
python3 -m http.server 9100 --directory /tmp/rpcbench

# 3. Prometheus with scrape job → host.docker.internal:9100/metrics.prom (terminal B)
# 4. Grafana as above (terminal C)
# 5. Re-run step 1 later; wait for the next scrape; panels update
```

## Grafana Cloud

1. Ship the textfile into Cloud (Grafana Agent / Alloy scraping the same HTTP path or node_exporter).
2. In Cloud Grafana: **Import** → `grafana/dashboards/rpcbench.json`.
3. Bind the Cloud Prometheus datasource.
4. Same variables and panels as local.

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

Names must match [PROMETHEUS.md](PROMETHEUS.md). Empty panels usually mean the scrape target is wrong, the file was never overwritten after a run, or the datasource URL is unreachable from the Grafana container.
