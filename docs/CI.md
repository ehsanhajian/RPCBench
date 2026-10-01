# Docker and GitHub Action

Run RPCBench in CI or a container, upload HTML / JSON / MD, and **fail the job** when an SLO budget misses.

Also see the [README](../README.md) quick start and [METHODOLOGY](METHODOLOGY.md).

## SLO budgets

| Flag | Meaning |
| --- | --- |
| `--ci` / `--strict` | Exit `1` when a budget misses (reports still write) |
| `--max-p95 MS` | Max P95 latency (ms) |
| `--max-error-rate FRAC` | Max error rate (`0`–`1`) |
| `--max-lag BLOCKS` | Max head lag vs cohort |
| `--slo-endpoint NAME` | Check only this endpoint (default: all) |
| `--out-dir DIR` | Write `report.json`, `report.html`, `report.md`, `metrics.prom` |

```bash
rpcbench compare --endpoints endpoints.yaml --out-dir out \
  --ci --max-p95 500 --max-error-rate 0.05
echo $?          # 1 if any checked endpoint missed a budget
open out/report.html
```

Stderr shows `SLO  ok` or `SLO  failed` plus which budget missed.

## Docker

Image: `ghcr.io/ehsanhajian/rpcbench` (published on `v*` tags).

```bash
docker pull ghcr.io/ehsanhajian/rpcbench:latest
docker run --rm -v "$PWD:/work" -w /work ghcr.io/ehsanhajian/rpcbench:latest \
  compare --endpoints endpoints.yaml --out-dir out --ci --max-p95 500
```

Local build: `docker build -t rpcbench .`

## GitHub Action

Composite action at the repo root (`action.yml`). Artifacts upload as `rpcbench-report`.

```yaml
name: rpcbench
on:
  schedule:
    - cron: "0 */6 * * *"
  pull_request:
jobs:
  compare:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ehsanhajian/RPCBench@v0.6.1
        with:
          endpoints: endpoints.yaml
          budget: short
          max-p95: "500"
          max-error-rate: "0.05"
          # max-lag: "3"
          output-dir: rpcbench-out
```

Pin a release tag (`@v0.6.1` or later). Omit `version` for latest PyPI, or set it to match the Action tag.

**Absolute vs relative gates:** the Action checks SLO budgets. For regressions vs a previous run, keep JSON history and add `rpcbench diff` as a second step (exit `1` when the previous primary got worse beyond the similar-band).
