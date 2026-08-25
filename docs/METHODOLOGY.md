# Methodology

What RPCBench numbers are — and what they are not. Tool split: [BOUNDARY.md](BOUNDARY.md).

## Clocks and samples

Latency uses a monotonic clock. Warmup is excluded from stats. Percentiles, jitter, min/mean/max, and the histogram use **successful** samples only. Error rate is failed/attempted.

## Method mix

Default CLI is still one method (`eth_blockNumber`). **`--profile mix`** is the documented production-like workload. `--samples` and `--warmup` apply **per method**. Ranking, Comparison, and Fastest use **all mix samples together**, not only head. The Methods table is per step.

Fixed payloads (same on every provider):

| Step | Method | Params |
| --- | --- | --- |
| head | `eth_blockNumber` | `[]` |
| chainId | `eth_chainId` | `[]` |
| block | `eth_getBlockByNumber` | `["latest", false]` |
| balance | `eth_getBalance` | `[0x000…0000, "latest"]` |
| call | `eth_call` | `[{"to": 0x000…0000, "data": "0x"}, "latest"]` |
| logs | `eth_getLogs` | `[{fromBlock, toBlock: "latest", address: 0x000…0000}]` |

Logs are one block and one address. No unbounded scans. No writes.

## Sample budgets

`--budget short|standard|long` is how long we sample, not a Nodeprobe scan profile.

| Budget | Samples | Warmup | Timeout | Max duration | Concurrency |
| --- | --- | --- | --- | --- | --- |
| **short** | 3 | 0 | 5s | 30s | all providers (`0`) |
| **standard** (default) | 10 | 1 | 10s | 600s | all providers (`0`) |
| **long** | 50 | 2 | 15s | 1800s | all providers (`0`) |

`--samples`, `--warmup`, `--timeout`, `--max-duration`, and `--concurrency` override the table. HTTP cap is `--max-requests` (default 128).

`long` does not add archive, history, WebSocket, or tracing. Those methods appear only when the workload asks (for example `--profile mix` already includes bounded logs; there is no tracing mix yet).

## Paired compare

Default compare is **paired**: one shared read-only sequence; each sample is raced to every provider. `--sequential` is A-then-B (heads and caches can drift).

## Ranking and similar-band

Default rank key is **P95** of successes (`--rank-by` for p50, p99, mean, or rps).

**Similar-band** (default **10%**, `--similar-band 0.10`): two values are similar if the worse is within that fraction of the better. Similar endpoints **share a place**. Fastest is that place-1 set. 81ms vs 84ms is not a victory.

An endpoint whose **error rate is above the same band**, whose head is **stale**, or whose pinned block **hash disagrees** with the cohort, does not get a numbered place or Fastest. It is listed after placed rows as `~`. Failed endpoints (`n_ok=0`) stay last.

We use this documented band instead of bootstrap confidence intervals. Typical `--samples 10` is too small for a stable P95 CI.

## Head freshness

Each provider’s head is the first timed `eth_blockNumber` in the workload (default and `--profile mix` already include it). If the workload has no `eth_blockNumber` (for example `--preset balance`), one extra paired `eth_blockNumber` wave runs after the timed samples. Extra heads are **not** mixed into latency stats.

**Cohort tip** is the upper median of known heights (`ordered[len // 2]`). Two providers at 90 and 100 → tip 100.

**Lag** is `max(0, tip − height)` blocks. Ahead of the median is lag 0, fresh. Estimated time is `lag_blocks ×` seconds per block. `--block-time` overrides. If the mix already returned `eth_chainId`, a small known-chain table is used (Ethereum 12s, Polygon/Base/Optimism 2s, BNB 3s, Arbitrum 0.25s). Otherwise 12s.

**Stale** when `lag_blocks > --stale-blocks` (default 2; strictly exceeds). Unknown when the head could not be parsed. This is a compare-time freshness verdict, not ValidatorPulse monitoring and not a reading of `eth_syncing`.

## Head hash consistency

After freshness, one paired `eth_getBlockByNumber(pin, false)` is sent to every provider. Extra hashes are **not** mixed into latency stats.

**Pin** is `--block` (hex, decimal, `latest`, or `cohort`) when set. Otherwise the cohort median head from this run. Use `--block` when heads naturally diverge by one block.

**Canonical hash** is the unique majority among parsed hashes. A 1–1 split has no canonical hash: both **disagree**. Matching the majority is **agree**. Missing or unparseable results (including `null`) are **unknown**, not disagree — stale already covers a node that does not have the tip.

This is compare-time data agreement, not consensus fork choice and not a security finding.

## Client label and block tags

`web3_clientVersion` is stored as a **label** when the node volunteers a string. It is omitted when missing or not a client string. RPCBench does not flag outdated versions, CVEs, or “version disclosed” — that is Nodeprobe.

**Tags** are one paired snapshot of `eth_getBlockByNumber` for `latest`, `safe`, and `finalized` (second param `false`). Extra tag reads are **not** mixed into ranking samples. Freshness is per tag vs that tag’s cohort median (finalized is behind `latest` by design). JSON-RPC errors on a tag are **skipped** with reason `unsupported`; an empty/`null` result is `empty`. A family that has no `safe`/`finalized` still ranks on the timed workload.

To time a single tag with full `--samples`, pass `--method eth_getBlockByNumber --params '["finalized", false]'`.

## Rate-limit reliability

HTTP **429** and JSON-RPC messages that are clearly CU/throttle (`too many requests`, `rate limit`, `compute unit`, …) are class **`rate_limit`**, not a generic 4xx. 401/403 stay `http_4xx` (missing key, not a throttle). RPCBench does not harvest rate-limit headers as a security check.

**`--burst N`** (default 0, max 8) overlaps the first N **timed** samples already in the budget, then runs the rest as a steady phase. **`--rps`** caps how often steady samples start (`0` = as fast as responses allow). Neither flag adds requests or searches for a ceiling. Burst vs steady error rate and recovered rps are reported only when `--burst` is set. Extra `latest`/`safe`/`finalized` 429s are listed as `tags=N` on that table; they do not change timed n/err. Load shapes (ramp/spike/soak) are separate.

## P99

Nearest-rank P99 is the **slowest success** until **n ≥ 100**. Below that it is flagged (`p99_reliable: false`). Default `--samples 10` is not enough for P99.

## Jitter and histogram

Jitter is the sample standard deviation (needs n≥2). Histogram buckets: `<50ms`, `<100ms`, `<250ms`, `<1s`, `≥1s`.

## HTTP timing

Each successful sample is split where the HTTP stack allows it:

| Phase | What it is |
| --- | --- |
| **handshake** | DNS + TCP + TLS. **0** when keep-alive reuses the socket |
| **server** | Time from a ready connection to response headers (TTFB minus handshake) |
| **payload** | Body download + JSON parse |

Default is **keep-alive** (one pooled client per run). **`--new-connection`** closes the socket after every request so handshake is paid every time. Ranking still uses total round-trip, not a phase. TLS here is handshake latency, not a certificate or CORS check.

JSON includes p50/p95/p99 for each phase on the provider.

## Non-claims

Not an SLA. Not a security audit. Not geographic unless you run from more than one machine. Sequential `rps` is `1000 / mean_ms`, not parallel throughput.
