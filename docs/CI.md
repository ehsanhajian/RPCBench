# Docker and GitHub Action

Zero-setup CI gate: run RPCBench in Actions or a container, upload HTML/JSON/MD, fail when SLO budgets miss.

## CLI budgets (#28)

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
echo $?   # 1 if any checked endpoint missed a budget
open out/report.html
```

Misses print on stderr (`SLO  failed` + which budget). Passing runs print `SLO  ok`.

## Docker

Image: `ghcr.io/ehsanhajian/rpcbench` (published on `v*` tags).

```bash
docker run --rm -v "$PWD:/work" -w /work ghcr.io/ehsanhajian/rpcbench:latest \
  compare --endpoints endpoints.yaml --out-dir out --ci --max-p95 500
```

Build locally: `docker build -t rpcbench .`

## GitHub Action

Composite action at the repo root (`action.yml`). Inputs map to CLI flags; artifacts upload as `rpcbench-report`.

```yaml
- uses: ehsanhajian/RPCBench@v0.6.1
  with:
    endpoints: endpoints.yaml
    workload: general
    budget: short
    max-p95: "500"
    max-error-rate: "0.05"
    max-lag: "3"
    output-dir: rpcbench-out
```

Pin a release tag (e.g. `@v0.6.1`). Omit `version` for latest PyPI, or set it to match the Action tag.

Schedule or PR:

```yaml
on:
  schedule:
    - cron: "0 */6 * * *"
  pull_request:
```

Diff regressions: keep JSON history and use `rpcbench diff` (exit `1` when the previous primary got worse beyond the similar-band). The Action focuses on absolute SLO budgets; wire `diff` as a second step if you need relative gates.
