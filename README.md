# RPCBench

**Which RPC endpoint is fastest — for this call, from this machine?**

A small CLI that compares **EVM JSON-RPC over HTTP**: latency, P50/P95/P99, error rate, a ranked table, and JSON. Read-only by default. No accounts. No telemetry. Localhost and RFC1918 are allowed (that is how you bench your own node).

It is **not** a security scanner ([Nodeprobe](https://github.com/ehsanhajian/nodeprobe)) and **not** validator monitoring ([ValidatorPulse](https://github.com/ehsanhajian/ValidatorPulse)). Split: [docs/BOUNDARY.md](https://github.com/ehsanhajian/RPCBench/blob/main/docs/BOUNDARY.md). How the numbers are computed: [docs/METHODOLOGY.md](https://github.com/ehsanhajian/RPCBench/blob/main/docs/METHODOLOGY.md). Roadmap: [issues](https://github.com/ehsanhajian/RPCBench/issues) · epic [#19](https://github.com/ehsanhajian/RPCBench/issues/19).

## Install

```bash
pip install rpcbench
```

Python 3.10+. `rpcbench --version` prints `0.1.1`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

PRs run `pytest`, then a live smoke against PublicNode and dRPC (`--samples 1 --warmup 0`). No local node in CI; the smoke passes if either public endpoint is ok.

## Start

```bash
rpcbench compare --endpoints https://ethereum.publicnode.com --budget short
rpcbench compare --endpoints endpoints.yaml --profile mix --budget short
```

Or a YAML/JSON file of named endpoints (keep API keys in a **local** file; do not commit it):

```yaml
endpoints:
  - name: publicnode
    url: https://ethereum.publicnode.com
  - name: drpc
    url: https://eth.drpc.org
  - name: paid
    url: https://eth.example/v3/YOUR_KEY
    bearer: YOUR_TOKEN
    headers:
      X-Api-Key: YOUR_KEY
```

```bash
rpcbench run --endpoints endpoints.yaml
rpcbench compare --endpoints endpoints.yaml --json
rpcbench run --endpoints endpoints.yaml -o report.json
```

`run` and `compare` are the same command.

## Report

The CLI prints, in order:

1. **Summary** — Fastest (P95 by default; similar-band co-winners, not 81ms vs 84ms)
2. **Comparison** — same numbers in **your YAML order** (failed rows stay in place; head / lag / fresh / hash / match)
3. **Ranking** — ordered by `--rank-by`; similar share a place; high error, stale, or disagree is `~`; failed last
4. **Methods** — per-method P50/P95/P99 and errors when `--profile mix` (ranking still uses the whole mix)
5. **Tags** — one paired `latest` / `safe` / `finalized` snapshot (skipped with a reason if the tag is missing)
6. **Burst** — burst vs steady error rate and recovered rps when `--burst` is set (same request budget)
7. **Providers** — redacted URL + `id=` hash, client label, samples, errors, jitter, histogram; head lag as **caught up** or **stale**; hash as **matches group** or **differs from group**
8. **Capabilities** — who answered this method

On a TTY, ok is green and fail is red (`NO_COLOR` or a pipe turns color off). Reports never print API keys, bearer tokens, or header values.

## How a run works

- **Paired by default:** one shared read-only sequence; each sample is raced to every provider at the same time. `--sequential` is A-then-B.
- **Warmup is excluded** from min/mean/max, jitter, percentiles, error rate, and the histogram.
- **P50/P95/P99** are nearest-rank over successful samples. **Jitter** is the sample standard deviation of those samples (needs n≥2). **P99** is the slowest sample until n≥100 (flagged below that).
- **Histogram** buckets: `<50ms`, `<100ms`, `<250ms`, `<1s`, `≥1s` (same edges in JSON for a later HTML report).
- **Error rate** is failed/attempted, with a class (timeout, connection, HTTP 4xx/5xx, **rate_limit**, JSON-RPC, malformed). `rate_limit` is HTTP 429 or a CU/throttle JSON-RPC message — reliability, not a scan.
- **`--budget`** picks a named size (`short` / `standard` / `long`). That sets how many samples to take. **`--max-requests`** is the HTTP cap (how many requests the run may send). `--samples` and `--warmup` override the named size. `long` is more samples only — not archive, WebSocket, or tracing unless the workload asks.
- **`--profile mix`** runs a documented read-only mix (head, chainId, getBlockByNumber latest, getBalance of the zero address, eth_call of empty data to the zero address, getLogs latest→latest on the zero address). `--samples` is per method. Ranking uses the whole mix, not one cheap head read. Payloads: [docs/METHODOLOGY.md](docs/METHODOLOGY.md).
- **Ranking** is P95 of successes (over the mix when `--profile mix`). Override with `--rank-by p50|p95|p99|mean|rps` (`throughput` = `rps`). Lower latency wins; higher rps wins. **Similar-band** (default 10%) shares a place when the worse value is within that fraction of the better. Error rate above the same band, a **stale** head, or a **disagreeing** block hash is not a numbered place (`~`). Failed (`n_ok=0`) never take Fastest.
- **Freshness** is lag vs the cohort’s upper-median `eth_blockNumber` in the same window. Default `--stale-blocks 2`. The provider line prints **caught up** when lag is within that tolerance, **stale** when it exceeds it. Lag time uses `--block-time` or a known chain from `eth_chainId` already in the mix (12s on Ethereum). Extra head reads happen only when the workload has no `eth_blockNumber`; they are not mixed into latency stats. JSON still uses `fresh` / `stale`.
- **Consistency** is whether providers return the same block **hash** at one pinned height (default: that cohort median). `--block HEX|N` pins the check when heads naturally diverge by one block. A unique majority hash is canonical; a split is disagreement for everyone who returned a hash. The provider line prints **matches group** or **differs from group**. Missing/unparseable hashes are unknown, not disagree. The extra `eth_getBlockByNumber` is not mixed into latency stats. JSON still uses `agree` / `disagree`. Not fork choice and not a security finding.
- **Client** is a volunteered `web3_clientVersion` string stored as a label (Erigon vs Geth). Missing or hex-only results are omitted. Not a disclosure finding, not outdated-client recon, not a CVE check.
- **Tags** are one paired `eth_getBlockByNumber` snapshot each for `latest`, `safe`, and `finalized`. Latency and freshness are per tag vs that tag’s cohort. Unsupported tags are skipped with a reason and do not change Fastest. Full P95 of one tag is `--method eth_getBlockByNumber --params '["finalized", false]'`.
- **Burst** is opt-in (`--burst N`, max 8). The first N timed samples overlap; the rest are a steady phase, optionally capped with `--rps`. Burst splits the existing sample budget and does not add requests. Burst vs steady error rate and rps are reported separately. Default is off (`--burst 0`, `--rps 0`). Ramp/spike/soak shapes are a later issue.
- **rps** is `1000 / mean_ms` for this probe — not parallel throughput. `--rps N` is a start cap after `--burst`, not that formula.
- **JSON** (`--json` or `-o FILE`) includes `mode`, `seed`, `sequence_id`, per-sample `pairs` (body hashes), `jitter_ms`, `histogram`, `freshness`, `consistency`, `client`, `tags`, `burst`, and `phases`. Reliability `score` is success rate.

## Flags

```bash
rpcbench run --endpoints endpoints.yaml --budget short
rpcbench run --endpoints endpoints.yaml --profile mix --budget standard --max-requests 512
rpcbench compare --endpoints http://127.0.0.1:8545
rpcbench run --endpoints endpoints.yaml --rank-by p95
rpcbench run --endpoints endpoints.yaml --sequential
rpcbench run --endpoints endpoints.yaml --verbose --json
```

`--budget` is a **named size** (how long to sample). `--max-requests` is the **HTTP cap**. `--samples` / `--warmup` override the named size.

| `--budget` | Samples | Warmup | Timeout | Stop after |
| --- | --- | --- | --- | --- |
| `short` | 3 | 0 | 5s | 30s |
| `standard` (default) | 10 | 1 | 10s | 600s |
| `long` | 50 | 2 | 15s | 1800s |

| Flag | Default | |
| --- | --- | --- |
| `--budget` | `standard` | Named size in the table above |
| `--samples` | 10 | Timed requests per method (overrides `--budget`) |
| `--warmup` | 1 | Requests excluded from stats (overrides `--budget`) |
| `--timeout` | 10s | Per-request timeout (overrides `--budget`) |
| `--max-requests` | 128 | HTTP cap for the whole run (hard cap `RPCBENCH_MAX_REQUESTS`, default 10000) |
| `--max-duration` | 600s | Stop and still print a report (overrides `--budget`; `0` = no limit) |
| `--concurrency` | 0 | Paired-wave cap (`0` = all providers). Not a load burst |
| `--burst` | 0 | Overlap the first N timed samples (`0`=off, max 8). Same request budget |
| `--rps` | 0 | Cap starts/sec after `--burst` (`0`=off). Does not raise the budget |
| `--seed` | 0 | Shared sequence stamp |
| `--rank-by` | `p95` | `p50`, `p95`, `p99`, `mean`, or `rps` |
| `--similar-band` | `0.10` | Relative band on the rank key (10%). High error above this is `~`, not a place |
| `--stale-blocks` | `2` | Head lag (blocks vs cohort median) above this is stale. Set per chain |
| `--block-time` | `12` or known chain | Seconds per block for estimated lag time |
| `--block` | cohort median | Pin the head-hash check (`hex`, decimal, or `latest`) |
| `--preset` | | `head` (`eth_blockNumber`), `chainId`, or `balance` (`eth_getBalance` of the zero address) |
| `--profile` | | `mix` — head, chainId, block, balance, call, bounded logs. Do not combine with `--method` or `--preset` |
| `--method` / `--params` | `eth_blockNumber` | JSON-RPC method and JSON array of params. Do not combine `--method` with `--preset` |
| `--allow-writes` | off | Required for write methods (`eth_send*`, `personal_*`, …) |
| `--verbose` | off | Print each sample |
| `--json` / `-o FILE` | | JSON to stdout, and/or write JSON to a file (table still prints unless `--json`) |
| `--sequential` | off | Run endpoints back-to-back instead of paired |

## Safety

Kill switch: `RPCBENCH_DISABLED=1`, or create `~/.config/rpcbench/DISABLED` (override path with `RPCBENCH_DISABLE_FILE`). RPCBench never prompts for a private key.

## License

[MIT](https://github.com/ehsanhajian/RPCBench/blob/main/LICENSE)
