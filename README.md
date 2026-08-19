# RPCBench

**Which RPC endpoint performs best — for this workload, from this region?**

RPCBench is a vendor-neutral CLI that measures RPC **quality** and compares providers: latency tails, freshness, integrity, method compatibility, rate limits, and a production-readiness verdict. It recommends a primary and a fallback. It does not guess; it publishes the methodology.

**Non-goals:** security auditing ([Nodeprobe](https://github.com/ehsanhajian/nodeprobe)) and always-on validator monitoring ([ValidatorPulse](https://github.com/ehsanhajian/ValidatorPulse)). Read-only by default. No accounts. No telemetry.

How the three tools split: [docs/BOUNDARY.md](docs/BOUNDARY.md). RPCBench never probes privileged namespaces, TLS/CORS, or client disclosure. It allows localhost (you are benchmarking your own node).

## What it measures

| Area | Signals |
| --- | --- |
| **Performance** | Latency, P50/P95/P99, jitter, histogram, DNS/TLS/TTFB split, HTTP/2, throughput, batch, concurrency, load shapes |
| **Workload** | General / wallet / indexer / trading / NFT mixes, custom YAML, on-chain payloads, capture/replay, getLogs ranges, simulation |
| **Capability** | Archive/history performance, WS subscribe latency, workload coverage, latest/safe/finalized, optional trace timing |
| **Honesty** | Paired interleaved compare, similar-band, body consistency, vantage/region, rate-limit reliability, head lag |
| **Decision** | Reliability score, ranking, production verdict, performance signals, primary + fallback |
| **Output** | CLI, live TUI, local web UI, HTML (charts/heatmap), JSON, CSV, Markdown, Prometheus, Grafana |
| **Families** | EVM (all chain IDs), Solana (+ gRPC first-seen), Substrate, Cosmos, Aptos, Sui, NEAR, Starknet, Bitcoin, TON |

Implementation is the [issue tracker](https://github.com/ehsanhajian/RPCBench/issues). Parent epic: [#19](https://github.com/ehsanhajian/RPCBench/issues/19).

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Pull requests and pushes to `main` run `pytest`, then a live smoke: `rpcbench run` against PublicNode and dRPC (`--samples 1 --warmup 0`). There is no local node in CI. The smoke passes if either public endpoint is ok.

## Quick start

```bash
cp endpoints.example.yaml endpoints.yaml
rpcbench run --endpoints endpoints.yaml
```

Localhost and RFC1918 are allowed — that is how you bench your own node. `run` (and `compare`) print a CLI report: **summary**, **ranking**, **per-provider metrics**, and **method coverage**. Ranking is by **mean of successful samples** (warmup excluded); failed endpoints are last; ties keep config order. On a TTY, ok is green and fail is red (`NO_COLOR` or a pipe disables this). Reports print a redacted URL plus a short hash (`id=`), never API keys, bearer tokens, or header values.

### Config

YAML or JSON. Names must be unique; URLs must be `http` or `https`. Optional `bearer` and `headers` are sent on every probe.

```yaml
endpoints:
  - name: local
    url: http://127.0.0.1:8545
  - name: publicnode
    url: https://ethereum.publicnode.com
  - name: paid
    url: https://eth.example/v3/YOUR_KEY
    bearer: YOUR_TOKEN
    headers:
      X-Api-Key: YOUR_KEY
```

`--endpoints` also accepts a single URL: `rpcbench compare --endpoints http://127.0.0.1:8545`.

### Flags

```bash
rpcbench run --endpoints endpoints.yaml --samples 10 --warmup 1 --preset head --timeout 10 --budget 128
rpcbench compare --endpoints endpoints.yaml
rpcbench compare --endpoints http://127.0.0.1:8545
rpcbench run --endpoints endpoints.yaml --verbose
rpcbench run --endpoints endpoints.yaml --json
rpcbench run --endpoints endpoints.yaml -o report.json
```

`--samples` timed requests per endpoint after `--warmup` (defaults: 10 and 1). Warmup is excluded from min/mean/max, percentiles, and error rate. P50/P95/P99 are nearest-rank over successful samples (the `n=` on that line). Error rate is failed/attempted with a small class breakdown (timeout, connection, HTTP 4xx/5xx, JSON-RPC, malformed). Ranking uses mean of successful samples (warmup excluded); failures print last; equal means keep config order. `--verbose` prints each sample. `--preset` is `head` (`eth_blockNumber`), `chainId`, or `balance` (`eth_getBalance` of the zero address). Or pass `--method` and optional `--params` (JSON array). Write methods are rejected unless `--allow-writes`.

`--json` prints a machine-readable report to stdout instead of the table. `-o FILE` writes that JSON to a file (the table still prints unless you also pass `--json`). The schema covers ranking, per-provider latency/percentiles, sequential `rps`, error classes, method capability, and a reliability `score` (success rate). URLs are redacted; only `url` + `id` are stored.

`--budget` is the max HTTP requests for the whole run, including warmup (default 128, hard cap `RPCBENCH_MAX_REQUESTS` default 10000). `--max-duration` stops remaining work after N seconds and still prints the report (default 600; `0` = no limit). `--concurrency` is 1. One bad URL or timeout fails that row only; if the budget or duration is spent, later endpoints are skipped and the report is still written.

Kill switch: set `RPCBENCH_DISABLED=1`, or create `~/.config/rpcbench/DISABLED` (override path with `RPCBENCH_DISABLE_FILE`). RPCBench never prompts for a private key.

Planned later: HTML reports and workload mixes.

## License

[MIT](LICENSE)
