# Methodology

What RPCBench numbers are — and what they are not. Tool split: [BOUNDARY.md](BOUNDARY.md).

## Clocks and samples

Latency uses a monotonic clock. Warmup is excluded from stats. Percentiles, jitter, min/mean/max, and the histogram use **successful** samples only. Error rate is failed/attempted.

## Method mix

Default CLI is still one method (`eth_blockNumber`). **`--profile mix`** is the documented production-like workload. `--samples` and `--warmup` apply **per method**. Ranking, Comparison, and Fastest use **all mix samples together**, not only head. The Methods table (`--verbose`) is per step.

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

## Workload coverage

Coverage is **this workload only**: each mix step is timed OK, an error class, or **skip** (JSON-RPC method not found / not offered). It is product fit, not a surface scan. `--profile mix` failing `eth_getLogs` is a coverage miss for an indexer, not a vulnerability. Missing required steps take `~` in Ranking (same as high error, stale, or disagree). Default catalogs never include admin/personal/miner/engine/txpool.

JSON `coverage` lists the same steps and cells. No `rpc_modules` walk. No `discover`.

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

An endpoint whose **error rate is above the same band**, whose head is **stale**, whose pinned block **hash disagrees** with the cohort, or whose mix is missing a required step, does not get a numbered place or Fastest. It is listed after placed rows as `~`. Failed endpoints (`n_ok=0`) stay last.

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

## Reliability score

0–100 for **this run**. Not an SLA. Not a security score. Deterministic for the same samples.

```
rel = round(
    50 × (1 − error_rate)
  + 20 × (1 − timeout_share)
  + 20 × (1 − tail)
  + 10 × coverage
)
```

Clamped to 0–100. `timeout_share` is timeout count / attempted. `tail` is 0 when P99/P50 = 1 and 1 when P99/P50 ≥ 3 (linear in between). `coverage` is the fraction of mix steps with n_ok > 0; a single method that answered is 1. A 100% error run is 0. A clean flat-tail run is 100.

JSON `reliability` includes the score, success_rate, the inputs, and `parts` (the four weighted terms). Ranking `rel` is that integer. `--verbose` prints the breakdown.

## Production-readiness verdict

A categorical decision for **this workload, this run**. Not an SLA. Not a security finding. Compact CLI always prints **Verdict**; `--verbose` adds **Signals** (problem / why / next — routing and config, not hardening).

Decisions: **ready** / **risky** / **not ready** (JSON: `ready`, `risky`, `not_ready`).

**not ready** if any of: no successful timed samples, stale head, mix coverage miss, or disagreeing pinned hash. Kind is `timeout` / `rate-limited` / `failed` when nothing succeeded, else `stale`, `coverage`, or `disagree`.

**risky** if the endpoint still answered but hit 429s, some timeouts, lag within `--stale-blocks` (`stale-risk`), jitter (stddev) above half of P50, or an error rate above the similar-band (when that is not already a timeout or 429).

**ready** otherwise: place-1 and similar-band co-winner → `similar`; place-1 alone → `fast+stable`; other numbered places → `slow+reliable`.

Each signal is `id`, `problem`, `why`, `next`. Next-actions are operational (raise `--timeout`, pick another endpoint, pin `--block`, localhost is allowed). JSON `verdict` is on ranking, comparison, and providers. Summary lists `ready_names`, `risky_names`, `not_ready_names` in ranking order.

## Primary and fallback

Production routing is two endpoints. **Route** names a **primary** and **fallback** from **ready** providers only (same `ready` as the verdict). Stale, disagree, coverage-miss, and failed endpoints never become primary.

Among ready endpoints in the similar-band of the fastest ready node, primary is the highest reliability score, then lower head lag, then a matching hash, then ranking order. Fallback is the next ready endpoint in ranking order. If primary had a timed error class, fallback skips others with that same class when a diverse ready alternative exists (so two 429s are not the pair).

When fewer than two providers are ready, fallback is omitted (`null` / `none`). Compact CLI prints one paragraph. JSON is `route` (`primary`, `fallback`, `why`) and `summary.primary` / `summary.fallback`. Not an SLA.

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

## Report watermark

Every JSON report includes a `watermark` object so the numbers can be cited:

| Field | Meaning |
| --- | --- |
| `version` | Tool version (`rpcbench --version`) |
| `git_sha` | Checkout SHA when this is a git install; omitted (`null`) from a PyPI wheel. `-dirty` if the tree has uncommitted diffs |
| `utc` | Run start, UTC (`YYYY-MM-DDTHH:MM:SSZ`) |
| `budget` | Named sample size (`short` / `standard` / `long`) |
| `workload` | `mix` or the JSON-RPC method |
| `seed` | Shared sequence stamp |
| `family` | RPC family (`evm` today) |
| `vantage` | `RPCBENCH_VANTAGE`, or the hostname |
| `samples` / `warmup` | Timed samples and excluded warmup, per method |
| `methodology` / `boundary` | This page and [BOUNDARY.md](BOUNDARY.md) |

The compact CLI prints a **Cite** line. `--verbose` prints the same doc URLs in the footer. `--html -o report.html` reuses the same watermark object in the footer — same links, not a scanner card. Ranking, P95, error rate, and freshness sit above the fold; P95 and histogram charts are inline SVG (no network).

Reproduce with the same `--budget`, `--profile`/`--method`, and `--seed` from a similar vantage. URLs in reports are redacted; JSON keeps a hash id, not the key.
