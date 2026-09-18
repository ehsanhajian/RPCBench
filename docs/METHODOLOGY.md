# Methodology

What RPCBench numbers are — and what they are not. Tool split: [BOUNDARY.md](BOUNDARY.md).

## Clocks and samples

Latency uses a monotonic clock. Warmup is excluded from stats. Percentiles, jitter, min/mean/max, and the histogram use **successful** samples only. Error rate is failed/attempted.

## Method mix

Default CLI is still one method (`eth_blockNumber`). **`--workload general|wallet|indexer|trading|nft|tracing`** is the documented production-like mix (`--workload` with no name is **general**). **`--profile mix`** is the old name for `--workload general`. `--budget short|standard|long` sets how many rounds to take. **`--samples` / `--warmup` apply per mix round**; each step’s **weight** is how many times that call appears in a round. Ranking, Comparison, and Fastest use **all mix samples together**, not only head. The Methods table (`--verbose`, and compact CLI when an optional step is in the mix) is per step.

An indexer winner is not a wallet winner: ranking and coverage are **this mix only**. Privileged debug recon (`debug_memStats`, `debug_verbosity`, …) is never in catalogs. `trace_*` and `debug_traceCall` are **`--workload tracing`** (or a YAML mix you write) only.

Family catalogs are **EVM** today. Other families error instead of sending `eth_*` at a Solana or Bitcoin URL.

Shared read-only payloads (same on every provider):

| Step | Method | Params |
| --- | --- | --- |
| head | `eth_blockNumber` | `[]` |
| chainId | `eth_chainId` | `[]` |
| block | `eth_getBlockByNumber` | `["latest", false]` |
| balance | `eth_getBalance` | `[0x000…0000, "latest"]` |
| call | `eth_call` | `[{"to": 0x000…0000, "data": "0x"}, "latest"]` |
| gas | `eth_estimateGas` | `[{"to": 0x000…0000, "data": "0x"}]` |
| logs | `eth_getLogs` | `[{fromBlock, toBlock: "latest", address: 0x000…0000}]` |
| trace | `trace_block` | `["latest"]` |
| debug | `debug_traceCall` | `[{to: 0x000…0000, data: "0x"}, "latest", {tracer: "callTracer", timeout: "1s"}]` |

Logs are one block and one address. No unbounded scans. No writes. Logs appear only in mixes that need them. `eth_estimateGas` appears in **wallet** and **trading** only. `eth_simulateV1` is not in these catalogs (`--simulate` or a YAML profile). `trace_block` is **`--workload tracing`** only: one recent block, `trace` types — not `vmTrace`, not `trace_filter`. `debug_traceCall` is the same mix: fixture call, `callTracer`, 1s timeout cap. Both are optional (not-offered / restricted / timeout is skip, not a ranking miss).

| `--workload` | What it models | Steps × weight |
| --- | --- | --- |
| **general** | Balanced dApp reads | head 1, chainId 1, block 1, balance 1, call 1, logs 1 |
| **wallet** | Balances, calls, and gas | head 1, chainId 1, block 1, balance 4, call 3, gas 2 |
| **indexer** | Blocks and bounded logs | head 1, chainId 1, block 3, call 1, logs 4 |
| **trading** | Fresh head, calls, and gas | head 3, chainId 1, block 2, call 4, gas 2 |
| **nft** | Calls and bounded logs | head 1, chainId 1, block 1, balance 1, call 3, logs 3 |
| **tracing** | Optional `trace_block` + `debug_traceCall` | head 1, chainId 1, block 1, trace 4, debug 2 |

Example: `--workload wallet --budget short` is 3 rounds × 12 weighted calls = 36 timed samples per endpoint (plus extra freshness/hash/tag reads). `--workload indexer --budget short` is 3 × 10, and four of every ten are bounded `eth_getLogs`.

## Custom YAML profiles

`--profile FILE.yaml` loads a mix without a code change. Named catalogs stay `--workload general|wallet|…|tracing` / `--profile mix`. Do not combine a file with `--method`, `--preset`, or `--params`.

```yaml
name: dex
notes: Uniswap-style reads
timeout: 8          # used when --timeout is omitted
# contract: "0x…"   # optional default for source: known_contract
methods:
  - method: eth_blockNumber
    weight: 1
  - method: eth_getBlockByNumber
    source: recent_block
    weight: 2
  - method: eth_getBalance
    source: seeded_address
    weight: 3
  - method: eth_call
    source: known_contract
  - method: eth_getLogs
    source: latest_head
```

`source` fills params from a shared snapshot taken **before** timed samples (not mixed into ranking):

| Source | What it fills | Fallback if the chain cannot supply data |
| --- | --- | --- |
| `latest_head` | Pin block tag to the cohort `eth_blockNumber` | `"latest"` |
| `recent_block` | A block in `[head − 256, head]` from `--seed` | `"latest"` |
| `known_contract` | WETH (or wrapped native) for chainId 1 / 10 / 8453 / 137, or YAML `contract:` | zero address |
| `seeded_address` | `0x` + SHA-256(`rpcbench:{seed}:{step}`)[:20] | (always determined; no chain read) |

The same `--seed` + profile + fetched head produces the same method/param sequence on every provider and on a re-run of that snapshot. A live head that moved between runs changes absolute block numbers; the offset and seeded address stay fixed. Static `params:` are allowed instead of `source`, not both on one step.

Logs from these sources stay **one block**. Unbounded scans are out of scope (`--logs-range` is the extra range table). `debug_memStats` / `debug_verbosity` / admin / txpool / `trace_filter` are rejected. `trace_block` / `trace_call` / `debug_traceCall` in YAML are always optional (unsupported / restricted / timeout is skip, not a ranking miss). Writes still need `--allow-writes`. `eth_simulateV1` in YAML is always optional (not-offered is skip, not a ranking miss).

JSON `payload` records `source` (`chain` / `seed` / `yaml` / `fixture`), `head`, `chain_id`, and `fallback`. Compact CLI, HTML, CSV, and markdown print the same facts.

## Workload coverage

Coverage is **this workload only**: each mix step is timed OK, an error class, or **skip** (JSON-RPC method not found / not offered). It is product fit, not a surface scan. `--workload indexer` failing `eth_getLogs` is a coverage miss for an indexer, not a vulnerability. `--workload wallet` does not send logs, so a node that cannot serve `eth_getLogs` can still look ready for a wallet. Missing **required** steps take `~` in Ranking (same as high error, stale, or disagree). **`eth_simulateV1`**, **`trace_*`**, and **`debug_traceCall`** are optional: unsupported / restricted / timeout is skip, not a ranking miss and not mixed into error rate. Default catalogs never include admin/personal/miner/engine/txpool, never include `debug_memStats` / `debug_verbosity`, never include writes or `eth_simulateV1`. `trace_*` and `debug_traceCall` are `--workload tracing` (or YAML) only.

JSON `coverage` lists the same steps and cells. No `rpc_modules` walk. No `discover`.

## Sample budgets

`--budget short|standard|long` is how long we sample, not a Nodeprobe scan profile.

| Budget | Samples | Warmup | Timeout | Max duration | Concurrency |
| --- | --- | --- | --- | --- | --- |
| **short** | 3 | 0 | 5s | 30s | all providers (`0`) |
| **standard** (default) | 10 | 1 | 10s | 600s | all providers (`0`) |
| **long** | 50 | 2 | 15s | 1800s | all providers (`0`) |

`--samples`, `--warmup`, `--timeout`, `--max-duration`, and `--concurrency` override the table. HTTP cap is `--max-requests` (default 128).

`long` does not add archive, history, WebSocket, or tracing. Those methods appear only when the workload asks (for example `--workload indexer` includes bounded logs; `--workload wallet` does not; `--workload tracing` times optional `trace_block` and `debug_traceCall`).

## Paired compare

Default compare is **paired**: one shared read-only sequence; each sample is raced to every provider. `--sequential` is A-then-B (heads and caches can drift).

## Ranking and similar-band

Default rank key is **P95** of successes (`--rank-by` for p50, p99, mean, or rps).

**Similar-band** (default **10%**, `--similar-band 0.10`): two values are similar if the worse is within that fraction of the better. Similar endpoints **share a place**. Fastest is that place-1 set. 81ms vs 84ms is not a victory.

An endpoint whose **error rate is above the same band**, whose head is **stale**, whose pinned block **hash disagrees** with the cohort, or whose mix is missing a required step, does not get a numbered place or Fastest. It is listed after placed rows as `~`. Failed endpoints (`n_ok=0`) stay last.

We use this documented band instead of bootstrap confidence intervals. Typical `--samples 10` is too small for a stable P95 CI.

## Head freshness

Each provider’s head is the first timed `eth_blockNumber` in the workload (default and the app mixes already include it). If the workload has no `eth_blockNumber` (for example `--preset balance`), one extra paired `eth_blockNumber` wave runs after the timed samples. Extra heads are **not** mixed into latency stats.

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

## JSON-RPC batch

**`--batch N`** (default 0 = off, omit N for 3, max 8) is an extra read after timed samples, tags, and client. RPCBench POSTs one JSON array of N copies of the primary workload method (ids `1..N`), then sends the same N calls one-by-one on the same keep-alive client. Reported numbers are wall-clock `batch_ms`, `serial_ms`, and `ratio = serial_ms / batch_ms` (>1 means the batch was faster). These are **not** mixed into ranking, reliability, or Fastest.

A JSON **array** means the provider accepted batch. A single JSON-RPC **object** (error or otherwise) is **`batch_unsupported`** — a capability result, not a crash. Item-level errors or missing ids are **`partial`**. HTTP 4xx/5xx stay those classes. Huge batches are out of scope; this is not an HTTP/2 multiplexing study.

Adds **1+N HTTP requests per endpoint**.

## getLogs range scaling

**`--logs-range N`** (default 0 = off, omit N for 1000, allowed 1 / 10 / 100 / 1000) is an extra read after the pinned head is known. Mix catalogs still send `eth_getLogs` as **one block** (`latest→latest`). The extra read uses a **fixed** filter on every provider: `address` is the zero address, no `topics`, `toBlock` is the cohort pin, `fromBlock` is `pin − N + 1`.

Ranges **1, 10, 100, and N** are sent as paired waves. If the pin is below `N − 1` (the chain is too short), that window is **skipped** with reason `head` instead of clamping to genesis (a clamped window would not be an N-block scan). `budget` / `duration` skips are the same as other extra reads. Non-EVM families skip with `family`.

Each cell records latency, JSON-RPC/HTTP error class, response bytes, log count, and **truncation** (a result list of ≥10000 items, or an error that clearly says the log query was too large). A provider that only fails at 1000 blocks is visible in this table. These samples are **not** mixed into ranking, reliability, or Fastest.

Adds **up to 4 HTTP requests per endpoint**.

## Read-only simulation

Trading and wallets live on “would this tx work?”, not `eth_blockNumber`. **`--simulate`** appends any missing `eth_call`, `eth_estimateGas`, and `eth_simulateV1` to the active mix (fixture `{to: zero, data: "0x"}`; simulateV1 is one `blockStateCalls` entry with `validation: false`). Never `eth_send*`. `--workload wallet` and `--workload trading` already include `eth_estimateGas`. `eth_simulateV1` is not in the core catalogs.

These are **timed mix steps** (Methods table, Coverage, ranking) except unimplemented simulateV1: **skip**, dropped from ranking error rate, not a crash. YAML may list the same methods; simulateV1 stays optional.

## Optional trace timing

Indexers need to know whether traces are **fast enough**, not whether a public node “leaked” `trace_*` or `debug_*`. **`--workload tracing`** times `trace_block("latest")` (one recent block, `trace` types only — not `vmTrace`, not `trace_filter`) and one cheap `debug_traceCall` (fixture `{to: zero, data: "0x"}`, `callTracer`, 1s timeout). general / wallet / indexer / trading / nft never send those methods. `--budget long` does not turn this on.

Missing, restricted (401/403 / disabled), or timed-out traces are **skip**, not a crash and not a vulnerability. They are not mixed into ranking error rate. YAML may include `trace_block` / `trace_call` / `debug_traceCall` (always optional); `trace_filter`, `debug_memStats`, `debug_verbosity`, and other debug recon stay rejected.

## Archive / historical state

Indexers and wallets need old state, not only `latest`. **`--archive`** is one extra read after the pin is known: `eth_getBalance` of the zero address at **genesis** (`0x0`). Mix catalogs stay on `"latest"`. `--budget long` does not turn this on.

If the cohort pin is below **128** blocks (a typical full-node prune window), the probe is **skipped** with reason `head` instead of calling a block the node still has. Non-EVM families skip with `family`.

Each endpoint is classified **yes** / **no** / **unknown** / **rate-limited**:

- **yes** — hex balance at genesis
- **no** — pruned / missing-state JSON-RPC (`missing trie node`, `historical state is not available`, …)
- **rate-limited** — HTTP 429 or CU/throttle
- **unknown** — timeout, connection, other errors, or skip

Missing archive is a capability result, not a crash and not a vulnerability. These samples are **not** mixed into ranking, reliability, or Fastest. Timed historical-vs-head latency is `--lookback` ([Historical queries](#historical-queries)).

Adds **1 HTTP request per endpoint**.

## Historical queries

A genesis yes/no is not enough for indexers: they also need how slow a **non-head** read is. **`--lookback N`** (default 0 = off, omit N for 1000) is an extra read after archive detection: the same `eth_getBalance` of the zero address at **pin − N**, then once at `"latest"` so the two latencies are comparable. Mix catalogs stay on `"latest"`. `--budget long` does not turn this on.

`--lookback` turns on `--archive`. A node classified **no** (pruned) skips when N ≥ 128 (the typical full-node window) with reason `archive`. A **rate-limited** archive probe skips with `rate_limit`. Shallower lookbacks still run — a Geth full node may keep the last ~128 blocks. If the pin is below N, the probe is **skipped** with reason `head`. Missing pin is `pin`. Non-EVM families skip with `family`. When pin − N is genesis and the archive probe already succeeded, that sample is reused instead of sending a second genesis read.

Reported per endpoint: historical latency, latest (head) latency, ratio (`hist / head`), error rate, and a status (`ok` / `skip/…` / error class). These samples are **not** mixed into ranking, reliability, or Fastest. Missing history is skip, not a crash and not a vulnerability.

Adds **up to 2 HTTP requests per endpoint** (plus the archive probe).

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

## HTTP transport

Each sample records the negotiated HTTP version (`1.1` or `2`), `Content-Encoding` (`gzip`, `br`, …), request bytes, and response **wire** bytes (the encoded size). Large `eth_getLogs` bodies and missing compression look like slow nodes. Ranking still uses total round-trip, not size. Default is HTTP/1.1. **`--http2`** asks for HTTP/2 via ALPN and falls back to 1.1 if the peer does not offer it. **`--http1`** forces HTTP/1.1. Not a TLS, CORS, or compression-as-security check.

JSON includes proto, encoding, and byte counts on each sample plus a provider `transport` summary. HTML plots size vs latency below the fold only when payloads actually differ (mix / `eth_getLogs`); a 50-byte head read is omitted. The scatter uses a **log X** (response bytes) so a 40-byte head and a 7kB `getBlock` are not stacked on the axis; dots are colored by method.

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
| `workload` | named mix (`general`, `wallet`, …), a YAML profile name, or the JSON-RPC method |
| `seed` | Shared sequence stamp |
| `family` | RPC family (`evm` today) |
| `vantage` | `RPCBENCH_VANTAGE`, or the hostname |
| `samples` / `warmup` | Timed rounds and excluded warmup rounds; each round sends `sum(weights)` calls |
| `methodology` / `boundary` | This page and [BOUNDARY.md](BOUNDARY.md) |

The compact CLI prints a **Cite** line. `--verbose` prints the same doc URLs in the footer. `--html -o report.html` reuses the same watermark object in the footer — same links, not a scanner card. Ranking (with sample sparklines), a coverage **heatmap**, **Signals**, P95, error rate, and freshness sit above the fold. Charts are inline SVG (no network). Print CSS keeps sections and SVG on one page. The heatmap is this workload’s coverage and latency (ok / skip / miss), not a method-inventory scan.

`--md` is the compact ranking as GitHub-flavored markdown (P95, error rate, freshness, verdict). `--csv` is one row per provider with the same ranking numbers (percentiles, rps, error rate, score, rank, verdict) — not per-sample rows. `rpcbench diff old.json new.json` compares two JSON watermarks: P95 delta, winner change, and new **signals**. CI exits 1 when the previous **primary**’s rank key got worse beyond the similar-band (same band as ranking; override with `--similar-band`). Not a security finding. `--history DIR` stores JSON snapshots locally for that diff.

Reproduce with the same `--budget`, `--workload`/`--profile`/`--method`, and `--seed` from a similar vantage. URLs in reports are redacted; JSON keeps a hash id, not the key.
