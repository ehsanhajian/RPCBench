# RPCBench

**Which RPC endpoint is fastest — for this call, from this machine?**

A small CLI that compares **EVM JSON-RPC over HTTP**: latency, P50/P95/P99, error rate, a ranked table, and JSON. Read-only by default. No accounts. No telemetry. Localhost and RFC1918 are allowed (that is how you bench your own node).

It is **not** a security scanner ([Nodeprobe](https://github.com/ehsanhajian/nodeprobe)) and **not** validator monitoring ([ValidatorPulse](https://github.com/ehsanhajian/ValidatorPulse)). Split: [docs/BOUNDARY.md](https://github.com/ehsanhajian/RPCBench/blob/main/docs/BOUNDARY.md). How the numbers are computed: [docs/METHODOLOGY.md](https://github.com/ehsanhajian/RPCBench/blob/main/docs/METHODOLOGY.md). Roadmap: [issues](https://github.com/ehsanhajian/RPCBench/issues) · epic [#19](https://github.com/ehsanhajian/RPCBench/issues/19).

## Install

```bash
pip install rpcbench
```

Python 3.10+. `rpcbench --version` prints `0.2.0`.

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
rpcbench compare --endpoints endpoints.yaml --profile mix --budget short --json
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

Default is Fastest, a production-readiness **Verdict**, and the ranked list. `--verbose` is the full dump (including **Signals**). `--json` / `-o` is always the complete payload. Every report prints a **Cite** line (version, git sha, family, vantage, UTC) so the numbers can be reproduced.

**Default**

![Default compact CLI](docs/images/cli-compact.svg)

**`--profile mix`** (Coverage table: which required methods succeeded)

```bash
rpcbench compare --endpoints endpoints.yaml --profile mix --budget short
```

![Mix profile compact CLI](docs/images/cli-mix.svg)

**HTTP timing** (`--new-connection --verbose`)

![HTTP timing table](docs/images/cli-timing.svg)

**`--verbose`**

![Verbose CLI](docs/images/cli-verbose.svg)

The default CLI prints, in order:

1. **Summary** — Fastest (P95 by default; similar-band co-winners, not 81ms vs 84ms)
2. **Verdict** — production-ready / risky / not ready for this workload, this run. Labels: `fast+stable`, `slow+reliable`, `similar`, `stale`, `stale-risk`, `timeout`, `rate-limited`, `coverage`, `disagree`, `jitter`, `failed`, `errors`. Not an SLA.
3. **Ranking** — one table, ordered by `--rank-by`; similar share a place; high error, stale, or disagree is `~`; failed last. **`rel`** is the 0–100 reliability score for this run.
4. **Notes** — one line per endpoint that hit `rate_limit` on timed samples or tags (`merkle  rate_limit=2  tags=2`)

`--verbose` adds the rest (same numbers, no data loss):

5. **Comparison** — YAML order (failed rows stay in place; head / lag / fresh / hash / match; **rel**)
6. **Reliability** — breakdown of `rel` (errors, timeouts, tail, mix coverage). Not an SLA. Not a security score.
7. **Signals** — each problem / why / next (routing and config: raise `--timeout`, pick another endpoint, pin `--block`). Not CVE language, not hardening.
8. **Coverage** — active mix only: each required method is `ok`, an error class, or `skip` if not offered. A miss is product fit (indexer `eth_getLogs` 404s), not a vuln. Compact `--profile mix` prints this table; JSON is `coverage`.
9. **Methods** — per-method P50/P95/P99 and errors when `--profile mix` (ranking still uses the whole mix)
10. **Timing** — handshake (DNS+TCP+TLS) vs server wait vs payload (body+parse). Not mixed into ranking. Default is keep-alive; `--new-connection` is a cold handshake every request
11. **Tags** — one paired `latest` / `safe` / `finalized` snapshot (skipped with a reason if the tag is missing)
12. **Burst** — burst vs steady error rate and recovered rps when `--burst` is set (same request budget). Extra tag 429s show as `tags=N`, not in timed `n`/`err`.
13. **Providers** — one table: redacted URL, client, n/err, p95, head/lag/fresh/match, histogram (`≥1s=3`), note. Per-sample rows follow.
14. **Capabilities** — who answered this method

On a TTY, ok is green and fail is red (`NO_COLOR` or a pipe turns color off). Reports never print API keys, bearer tokens, or header values.

## How a run works

Numbers and caveats: [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

### Workload

- **Paired by default:** one shared read-only sequence; each sample is raced to every provider at the same time. `--sequential` is A-then-B.
- **`--budget`** picks a named size (`short` / `standard` / `long`). That sets how many samples to take. **`--max-requests`** is the HTTP cap (how many requests the run may send). `--samples` and `--warmup` override the named size. `long` is more samples only — not archive, WebSocket, or tracing unless the workload asks.
- **`--profile mix`** runs a documented read-only mix (head, chainId, getBlockByNumber latest, getBalance of the zero address, eth_call of empty data to the zero address, getLogs latest→latest on the zero address). `--samples` is per method. Ranking uses the whole mix, not one cheap head read. **Coverage** is those methods only: `ok`, error class, or `skip` if not offered. A failing `eth_getLogs` is a miss for an indexer, not a vuln. Payloads: [docs/METHODOLOGY.md](docs/METHODOLOGY.md).
- **Burst** is opt-in (`--burst N`, max 8). The first N timed samples overlap; the rest are a steady phase, optionally capped with `--rps`. Burst splits the existing sample budget and does not add requests. Burst vs steady error rate and rps are reported separately. Tag 429s are `tags=N` on that table (not mixed into timed n/err). Default is off (`--burst 0`, `--rps 0`). Ramp/spike/soak shapes are a later issue.

### Stats

- **Warmup is excluded** from min/mean/max, jitter, percentiles, error rate, and the histogram.
- **P50/P95/P99** are nearest-rank over successful samples. **Jitter** is the sample standard deviation of those samples (needs n≥2). **P99** is the slowest sample until n≥100 (flagged below that).
- **Histogram** is where successes landed: empty buckets are omitted (`≥1s=3`, or `<50ms=8  ≥1s=8` when split). Buckets: `<50ms`, `<100ms`, `<250ms`, `<1s`, `≥1s` (same edges in JSON).
- **HTTP timing** splits each successful sample into handshake (DNS+TCP+TLS), server wait after the connection is ready, and payload (body download + JSON parse). Ranking still uses total RTT. Default reuses keep-alive connections (handshake is ~0 after warmup). `--new-connection` opens a fresh TCP/TLS session every request so distance vs node time is visible. TLS here is handshake latency, not a certificate check.
- **Error rate** is failed/attempted, with a class (timeout, connection, HTTP 4xx/5xx, **rate_limit**, JSON-RPC, malformed). `rate_limit` is HTTP 429 or a CU/throttle JSON-RPC message — reliability, not a scan.
- **rps** in the table is `1000 / mean_ms` for this probe — not parallel throughput. `--rps N` is a start cap after `--burst`, not that formula.
- **Reliability `rel`** is 0–100 for **this run** (not an SLA, not a security score). Same samples always produce the same score:

  `rel = round( 50×(1−error_rate) + 20×(1−timeout_share) + 20×(1−tail) + 10×coverage )`

  `timeout_share` is timeouts / attempted. `tail` is 0 when P99=P50 and 1 when P99/P50 ≥ 3. `coverage` is the fraction of mix steps with at least one success (1.0 for a single method that answered). A 100% error run is **0**. A clean run with a flat tail is **100**. `--verbose` prints the four parts. JSON is `reliability` (score plus breakdown).

### Ranking

- Default is P95 of successes (over the mix when `--profile mix`). Override with `--rank-by p50|p95|p99|mean|rps` (`throughput` = `rps`). Lower latency wins; higher rps wins.
- **Similar-band** (default 10%) shares a place when the worse value is within that fraction of the better. Error rate above the same band, a **stale** head, a **disagreeing** block hash, or a **coverage miss** (a required mix step never succeeded) is not a numbered place (`~`). Failed (`n_ok=0`) never take Fastest.

### Extra reads

These are not mixed into latency stats or Fastest.

- **Freshness** is lag vs the cohort’s upper-median `eth_blockNumber` in the same window. Default `--stale-blocks 2`. Tables print **yes** when lag is within that tolerance, **stale** when it exceeds it (Ranking note may add `~Ns`). Lag time uses `--block-time` or a known chain from `eth_chainId` already in the mix (12s on Ethereum). Extra head reads happen only when the workload has no `eth_blockNumber`. JSON still uses `fresh` / `stale`.
- **Consistency** is whether providers return the same block **hash** at one pinned height (default: that cohort median). `--block HEX|N` pins the check when heads naturally diverge by one block. A unique majority hash is canonical; a split is disagreement for everyone who returned a hash. Tables print **yes** / **no** under match. Missing/unparseable hashes are unknown, not disagree. JSON still uses `agree` / `disagree`. Not fork choice and not a security finding.
- **Client** is a volunteered `web3_clientVersion` string stored as a label (Erigon vs Geth). Missing or hex-only results are omitted. Not a disclosure finding, not outdated-client recon, not a CVE check.
- **Tags** are one paired `eth_getBlockByNumber` snapshot each for `latest`, `safe`, and `finalized`. Latency and freshness are per tag vs that tag’s cohort. Unsupported tags are skipped with a reason and do not change Fastest. Full P95 of one tag is `--method eth_getBlockByNumber --params '["finalized", false]'`.

### JSON

`--json` or `-o FILE` includes `mode`, `seed`, `sequence_id`, `connection` (`keepalive` or `new`), a `watermark` (version, git sha, UTC, budget, workload, seed, family, vantage, sample counts, plus [methodology](docs/METHODOLOGY.md) and [boundary](docs/BOUNDARY.md) URLs), `coverage` (active mix steps only), `reliability` (0–100 this-run score plus breakdown; not success rate alone), `verdict` (ready / risky / not_ready plus `kind` and problem/why/next `signals`), per-provider `id` (URL fingerprint, not printed in the CLI table), per-sample `pairs` (body hashes), `jitter_ms`, `histogram`, `freshness`, `consistency`, `client`, `tags`, `burst`, HTTP `timing` percentiles, and burst `phases`.

## Flags

```bash
rpcbench run --endpoints endpoints.yaml --budget short
rpcbench run --endpoints endpoints.yaml --profile mix --budget short
rpcbench run --endpoints endpoints.yaml --profile mix --budget standard --max-requests 512
rpcbench compare --endpoints http://127.0.0.1:8545
rpcbench run --endpoints endpoints.yaml --rank-by p95
rpcbench run --endpoints endpoints.yaml --burst 4 --rps 2
rpcbench run --endpoints endpoints.yaml --sequential
rpcbench run --endpoints endpoints.yaml --new-connection
rpcbench run --endpoints endpoints.yaml --verbose
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
| `--new-connection` | off | Fresh TCP/TLS every request. Default is keep-alive |
| `--seed` | 0 | Shared sequence stamp |
| `--rank-by` | `p95` | `p50`, `p95`, `p99`, `mean`, or `rps` |
| `--similar-band` | `0.10` | Relative band on the rank key (10%). High error above this is `~`, not a place |
| `--stale-blocks` | `2` | Head lag (blocks vs cohort median) above this is stale. Set per chain |
| `--block-time` | `12` or known chain | Seconds per block for estimated lag time |
| `--block` | cohort median | Pin the head-hash check (`hex`, decimal, or `latest`) |
| `--preset` | | `head` (`eth_blockNumber`), `chainId`, or `balance` (`eth_getBalance` of the zero address) |
| `--profile` | | `mix` — head, chainId, block, balance, call, bounded logs. Prints Coverage. Do not combine with `--method` or `--preset` |
| `--method` / `--params` | `eth_blockNumber` | JSON-RPC method and JSON array of params. Do not combine `--method` with `--preset` |
| `--allow-writes` | off | Required for write methods (`eth_send*`, `personal_*`, …) |
| `--verbose` | off | Full CLI report (Comparison, Reliability, Signals, Coverage, Timing, Tags, Burst, Providers, per-sample) |
| `--json` / `-o FILE` | | JSON to stdout, and/or write JSON to a file (table still prints unless `--json`) |
| `--sequential` | off | Run endpoints back-to-back instead of paired |

## Safety

Kill switch: `RPCBENCH_DISABLED=1`, or create `~/.config/rpcbench/DISABLED` (override path with `RPCBENCH_DISABLE_FILE`). RPCBench never prompts for a private key. Set `RPCBENCH_VANTAGE` to label the machine in the report watermark (default: hostname).

## License

[MIT](https://github.com/ehsanhajian/RPCBench/blob/main/LICENSE)
