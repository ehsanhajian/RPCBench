# Methodology

What RPCBench numbers are — and what they are not. Tool split: [BOUNDARY.md](BOUNDARY.md).

## Contents

1. [Clocks and samples](#clocks-and-samples)
2. [Method mix](#method-mix)
3. [Custom YAML profiles](#custom-yaml-profiles)
4. [Workload coverage](#workload-coverage)
5. [Sample budgets](#sample-budgets)
6. [Paired compare](#paired-compare)
7. [Ranking and similar-band](#ranking-and-similar-band)
8. [Freshness, consistency, client, tags](#head-freshness)
9. [Extra reads](#rate-limit-reliability) (burst, batch, concurrency, throughput, shapes, logs, simulate, archive, history, WS, Yellowstone)
10. [Capture and replay](#traffic-capture-and-replay)
11. [Stats](#p99) (P99, reliability, verdict, route, jitter, HTTP timing/transport)
12. [Non-claims](#non-claims)
13. [Vantage and multi-region](#vantage-and-multi-region)
14. [Report watermark](#report-watermark)

---

## Clocks and samples

Latency uses a monotonic clock. **Warmup is excluded** from stats. Percentiles, jitter, min/mean/max, and the histogram use **successful** samples only. Error rate is failed / attempted.

---

## Method mix

Default CLI is the **general** mix at **short** size when you pass only `--endpoints`.

| Flag | Role |
| --- | --- |
| `--workload wallet\|indexer\|trading\|nft\|tracing` | Named job (`wallet` / `trading` also simulate; `indexer` also logs-range / archive / lookback) |
| `--profile mix` | Alias for general |
| `--method` / `--preset` | Single call (head = `eth_blockNumber`) |
| `--budget short\|standard\|long` | How many rounds — does **not** turn extras on |
| `--samples` / `--warmup` | Apply **per mix round**; each step’s **weight** is how often it appears in a round |

Ranking, Comparison, and Fastest use **all mix samples together**, not only head. An indexer winner is not a wallet winner — ranking and coverage are **this mix only**.

Privileged debug recon (`debug_memStats`, `debug_verbosity`, …) is never in catalogs. `trace_*` and `debug_traceCall` are **`--workload tracing`** (or YAML) only.

### Families

One adapter per protocol family (not per EVM chain id). Unknown EVM chain ids still use the EVM mix.

| Family | Identity / head (examples) | Notes |
| --- | --- | --- |
| **evm** | `eth_chainId`, `eth_blockNumber` | One adapter for every chain id |
| **solana** | `getHealth`, `getSlot` | Optional Yellowstone via `grpc` URL |
| **substrate** | `system_health`, `chain_getHeader` | Polkadot / Kusama / parachains |
| **cosmos** | `status` | CometBFT JSON-RPC (not Cosmos EVM `eth_*`) |
| **aptos** | ledger GET `/v1` | REST fullnode |
| **sui** | `sui_getLatestCheckpointSequenceNumber` | WS subscribe skipped in default mix |
| **near** | `status` / `network_info` | WS skipped |
| **starknet** | `starknet_blockNumber` | WS skipped |
| **bitcoin** | `getblockchaininfo` | Cookie / Basic auth; ~10m block time |
| **ton** | `getMasterchainInfo` | API keys via headers stay out of reports |

`family: auto` / `--family auto` tries identity handshakes in order and names the family. That handshake is not a finding. EVM-only extras (logs-range, archive, lookback, eth simulate, tracing) skip with reason `family` on non-EVM.

### Shared EVM payloads

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
| debug | `debug_traceCall` | fixture call + `callTracer`, 1s timeout |

Logs are **one block**, one address. No unbounded scans. No writes. `eth_estimateGas` is **wallet** / **trading** only. `eth_simulateV1` is not in core catalogs (`--simulate` or YAML). Trace/debug are **tracing** only and optional (skip ≠ ranking miss).

| `--workload` | Models | Steps × weight |
| --- | --- | --- |
| **general** | Balanced dApp reads | head 1, chainId 1, block 1, balance 1, call 1, logs 1 |
| **wallet** | Balances, calls, gas | head 1, chainId 1, block 1, balance 4, call 3, gas 2 |
| **indexer** | Blocks + bounded logs | head 1, chainId 1, block 3, call 1, logs 4 |
| **trading** | Fresh head, calls, gas | head 3, chainId 1, block 2, call 4, gas 2 |
| **nft** | Calls + bounded logs | head 1, chainId 1, block 1, balance 1, call 3, logs 3 |
| **tracing** | Optional traces | head 1, chainId 1, block 1, trace 4, debug 2 |

Example: `--workload wallet --budget short` → 3 rounds × 12 weighted calls = 36 timed samples per endpoint (plus freshness / hash / tag extras).

---

## Custom YAML profiles

`--profile FILE.yaml` loads a custom mix. Do not combine a file with `--method`, `--preset`, or `--params`.

```yaml
name: dex
notes: Uniswap-style reads
timeout: 8
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

`source` fills params from a shared snapshot **before** timed samples (not mixed into ranking):

| Source | Fills | Fallback |
| --- | --- | --- |
| `latest_head` | Cohort `eth_blockNumber` pin | `"latest"` |
| `recent_block` | Block in `[head − 256, head]` from `--seed` | `"latest"` |
| `known_contract` | Wrapped native for known chainIds, or YAML `contract:` | zero address |
| `seeded_address` | `0x` + SHA-256(`rpcbench:{seed}:{step}`)[:20] | always determined |

Same `--seed` + profile + fetched head → same sequence on every provider. Live head movement between runs changes absolute block numbers; offset and seeded address stay fixed. Static `params:` and `source` are mutually exclusive per step.

Logs from these sources stay **one block**. Rejected in YAML: `debug_memStats` / `debug_verbosity` / admin / txpool / `trace_filter`. Optional: `trace_*` / `debug_traceCall` / `eth_simulateV1`. Writes need `--allow-writes`.

JSON `payload` records `source` (`chain` / `seed` / `yaml` / `fixture`), `head`, `chain_id`, and `fallback`.

---

## Workload coverage

Coverage is **this workload only**: each mix step is OK, an error class, or **skip** (method not offered). Product fit — not a surface scan.

- Required miss → `~` in Ranking (same as high error, stale, disagree)
- Optional (`eth_simulateV1`, `trace_*`, `debug_traceCall`) unsupported / restricted / timeout → skip, not a ranking miss

Default catalogs never include admin / personal / miner / engine / txpool, never `debug_memStats` / `debug_verbosity`, never writes. No `rpc_modules` walk. No `discover`.

---

## Sample budgets

`--budget` is how long we sample — not a Nodeprobe scan profile.

| Budget | Samples | Warmup | Timeout | Max duration |
| --- | --- | --- | --- | --- |
| **short** | 3 | 0 | 5s | 30s |
| **standard** | 10 | 1 | 10s | 600s |
| **long** | 50 | 2 | 15s | 1800s |

Overrides: `--samples`, `--warmup`, `--timeout`, `--max-duration`. HTTP cap: `--max-requests` (default 128). `--sequential` = A-then-B instead of paired.

`long` does **not** add archive, history, WebSocket, or tracing — only the workload does.

---

## Paired compare

Default is **paired**: one shared read-only sequence; each sample is raced to every provider. `--sequential` is A-then-B (heads and caches can drift).

---

## Ranking and similar-band

Default rank key: **P95** of successes (`--rank-by` for p50, p99, mean, or rps).

**Similar-band** (default **10%**): worse within that fraction of better → **share a place**. Fastest is that place-1 set. 81ms vs 84ms is not a victory.

No numbered place / Fastest when: error rate above the band, **stale** head, pinned hash **disagrees**, or required mix coverage miss → listed as `~`. Failed (`n_ok=0`) last.

We use this band instead of bootstrap CIs; typical `--samples 10` is too small for a stable P95 CI.

---

## Head freshness

Head = first timed `eth_blockNumber` in the workload (or one extra paired wave if the mix has none). Extra heads are **not** mixed into latency.

- **Cohort tip** = upper median of known heights
- **Lag** = `max(0, tip − height)`; ahead of median → lag 0
- **Stale** when `lag_blocks > --stale-blocks` (default 2)
- Lag time ≈ `lag_blocks ×` seconds/block (`--block-time`, or known chain from `eth_chainId`, else 12s)

Compare-time freshness — not ValidatorPulse, not `eth_syncing`.

## Head hash consistency

After freshness: one paired `eth_getBlockByNumber(pin, false)`. Pin = `--block` or cohort median.

- Unique majority hash → **agree** for matchers
- 1–1 split → everyone with a hash **disagrees**
- Missing / unparseable → **unknown** (not disagree)

Compare-time data agreement — not fork choice, not a security finding.

## Client label and block tags

`web3_clientVersion` is a **label** when volunteered. No outdated / CVE / “disclosed” findings.

**Tags:** one paired snapshot each for `latest`, `safe`, `finalized`. Not mixed into ranking. Unsupported tag → skip with reason. Full samples on one tag: `--method eth_getBlockByNumber --params '["finalized", false]'`.

---

## Rate-limit reliability

HTTP **429** and clear CU/throttle JSON-RPC messages → class **`rate_limit`**. 401/403 stay `http_4xx`.

**`--burst N`** (default 0, max 8) overlaps the first N **timed** samples already in the budget; rest are steady. **`--rps`** caps steady starts. Neither adds requests. Tag 429s show as `tags=N` (not timed n/err). See also [Load shapes](#load-shapes).

## JSON-RPC batch

**`--batch N`** (default off; omit N → 3; max 8): one JSON array of N primary-method calls, then N serial. Reports `batch_ms`, `serial_ms`, `ratio`. Not mixed into ranking.

- Array response → supported
- Single object → `batch_unsupported`
- Item errors → `partial`

Adds **1+N** requests/endpoint. Not an HTTP/2 study.

## Concurrent extra read

**`--concurrency N`** (default off; omit N → 4; max 8): N overlapping POSTs vs N serial. Reports concurrent P50/P95, ratio vs serial, errors. Not mixed into ranking. Adds **2N** requests/endpoint. Bounded — not a load generator.

## Throughput extra read

**`--throughput N`** (default off; omit N → 20; max 64): N serial POSTs; successful req/s, duration, completed. `--rps` caps starts. 429 = rejected, not a crash. Ranking table `rps` stays `1000 / mean_ms` of timed samples. Adds **N** requests/endpoint.

## Load shapes

**`--shape flat|ramp|spike|soak`**: bounded paced curve after timed samples. Series every 2s: RPS, P95, error rate. Not mixed into ranking.

| Shape | Duration | Max req | Cap | Curve |
| --- | --- | --- | --- | --- |
| `flat` | 8s | 32 | 2 | ~4 rps constant |
| `ramp` | 12s | 40 | 2 | 1 → 6 rps |
| `spike` | 10s | 40 | 4 | ~2 rps, spike ~8 for 2s |
| `soak` | 20s | 48 | 2 | ~2 rps |

Starts are serial. Short defaults — not a multi-hour soak. JSON / CLI / HTML / Prometheus `rpcbench_shape_*`.

## getLogs range scaling

**`--logs-range N`** (default off; omit N → 1000; allowed 1/10/100/1000): after pin, same zero-address `eth_getLogs` at 1, 10, 100, and N blocks. Mix logs stay one block. Too-short chain → skip `head`. Truncation recorded. Not mixed into ranking. Up to **4** requests/endpoint. Non-EVM → `family`.

## Read-only simulation

**`--simulate`** appends missing `eth_call` / `eth_estimateGas` / `eth_simulateV1` (fixture tx; never `eth_send*`). Wallet and trading turn this on. Unimplemented simulateV1 → **skip**, not a crash. Timed mix steps except optional simulateV1.

## Optional trace timing

**`--workload tracing`** times `trace_block("latest")` and a cheap `debug_traceCall`. Other workloads never send those. Missing / restricted / timeout → skip. Not a vulnerability. `--budget long` does not enable this.

## Archive / historical state

**`--archive`**: one `eth_getBalance` at genesis after pin. Classified **yes** / **no** / **unknown** / **rate-limited**. Pin below 128 blocks → skip `head`. Not mixed into ranking. Adds **1** request/endpoint. Indexer turns this on.

## Historical queries

**`--lookback N`** (default off; omit N → 1000): `eth_getBalance` at pin−N vs `latest`. Turns on archive detection; pruned nodes skip with `archive`. Reports hist/head latency ratio. Not mixed into ranking. Up to **2** requests (+ archive).

## WebSocket subscribe

**`--websocket SEC`** (default off; omit SEC → 3s; max 10s): connect optional `ws` / `websocket` URL, `eth_subscribe` `newHeads`, listen. Missing WS → **not configured**. Reports connect / subscribe / first-event ms, missed heads, disconnects. Does not consume `--max-requests`. Not mixed into ranking. Not a WS origin/auth check.

## Yellowstone / gRPC first-seen

**`--yellowstone SEC`** (Solana; default off; omit SEC → 3s; max 10s): race optional `grpc` / `yellowstone` slot streams. Missing gRPC → **not configured**. Needs `pip install 'rpcbench[yellowstone]'`. **Geography dominates** first-seen. Not tx landing; not an HTTP `getSlot` substitute. Not mixed into ranking. Does not replace HTTP Solana.

---

## Traffic capture and replay

| Command | Role |
| --- | --- |
| `rpcbench record -o FILE` | JSONL capture (`method` + `params`) of the same mix `run` would send |
| `rpcbench replay --from FILE` | Lockstep to every provider; compare status, error class, canonical body |

A call **matches** when successful `result` bodies agree (sorted-key JSON hash). Timeout / 429 → status/error on that row, not a body mismatch. `--verbose` prints unified diffs. Writes blocked unless `--allow-writes`. Not mixed into ranking. Adds **N × providers** requests.

---

## P99

Nearest-rank P99 is the **slowest success** until **n ≥ 100**. Below that: `p99_reliable: false`. Default `--samples 10` is not enough for P99.

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

`tail` is 0 when P99/P50 = 1 and 1 when P99/P50 ≥ 3 (linear in between). `coverage` = fraction of mix steps with at least one success. JSON `reliability` includes score, inputs, and `parts`.

## Production-readiness verdict

Categorical for **this workload, this run**. Compact CLI always prints **Verdict**; `--verbose` adds **Signals** (problem / why / next — routing, not hardening).

| Decision | When |
| --- | --- |
| **not ready** | No successes, stale head, coverage miss, or disagreeing hash |
| **risky** | Answered but 429s, some timeouts, lag within stale-blocks, high jitter, or high error rate |
| **ready** | Otherwise (`fast+stable`, `similar`, or `slow+reliable`) |

## Primary and fallback

**Route** picks **primary** + **fallback** from **ready** endpoints only. Stale / disagree / coverage-miss / failed never become primary.

Among ready endpoints in the similar-band of the fastest ready node: highest `rel`, then lower lag, then matching hash, then ranking order. Fallback prefers a different timed error class when possible. Fewer than two ready → fallback `none`.

## Jitter and histogram

Jitter = sample stddev (n≥2). Buckets: `<50ms`, `<100ms`, `<250ms`, `<1s`, `≥1s`.

## HTTP timing

| Phase | Meaning |
| --- | --- |
| **handshake** | DNS + TCP + TLS (≈0 on keep-alive reuse) |
| **server** | Ready connection → response headers |
| **payload** | Body download + JSON parse |

Default **keep-alive**. `--new-connection` pays handshake every request. Ranking uses total RTT. TLS here is latency, not a cert check.

## HTTP transport

Records negotiated HTTP version (`1.1` / `2`), `Content-Encoding`, request/response wire bytes. `--http2` / `--http1`. Ranking still uses total RTT. Not TLS/CORS-as-security.

---

## Non-claims

Not an SLA. Not a security audit. Not geographic unless you run from more than one machine.

- One compare = one vantage (`RPCBENCH_VANTAGE`) — not browser RUM / CWV
- Ranking `rps` = `1000 / mean_ms`, not parallel throughput
- `--throughput` / `--shape` / `--websocket` / `--yellowstone` / `replay` are bounded extras — not mixed into Fastest

---

## Vantage and multi-region

| Field | Source |
| --- | --- |
| `vantage` | `RPCBENCH_VANTAGE`, or hostname |
| `region` / `city` / `asn` | optional env |
| `hostname` | when available |

No extra CLI flags — set env on the measuring host.

```bash
RPCBENCH_VANTAGE=eu-west RPCBENCH_REGION=eu-west-1 \
  rpcbench compare --endpoints endpoints.yaml --seed 7 -o eu.json

RPCBENCH_VANTAGE=us-east RPCBENCH_REGION=us-east-1 \
  rpcbench compare --endpoints endpoints.yaml --seed 7 -o us.json

rpcbench merge eu.json us.json
rpcbench merge eu.json us.json --json -o merged.json
```

`merge` requires matching seed / workload / family. Prints per-vantage P95 and a global rollup (mean P95; max error rate).

---

## Report watermark

Every JSON report includes `watermark` so numbers can be cited:

| Field | Meaning |
| --- | --- |
| `version` | Tool version |
| `git_sha` | Git install SHA (`null` from PyPI wheel); `-dirty` if dirty tree |
| `utc` | Run start UTC |
| `budget` / `workload` / `seed` / `family` | Run identity |
| `vantage` (+ optional region/city/asn) | Where it was measured |
| `samples` / `warmup` | Timed / excluded rounds |
| `methodology` / `boundary` | Links to this page and [BOUNDARY.md](BOUNDARY.md) |

### Live UI and abort

- TTY: live table while sampling (`--plain` / `--ci` / pipe disables)
- `--web` / `rpcbench ui`: localhost UI; `--web-host 0.0.0.0` on a VPS (no auth)
- Ctrl-C or Stop: abort flag, finish current wave, still write reports (`aborted: true`)

### Formats

| Output | Role |
| --- | --- |
| Compact CLI | Cite + Summary / Verdict / Route / Ranking |
| `--verbose` | Full dump + signals / coverage / timing |
| `--html` | Offline charts, heatmap, signals, print CSS |
| `--md` / `--csv` | Pasteable table / flat rows |
| `--prometheus` | Textfile — [PROMETHEUS.md](PROMETHEUS.md) |
| `diff` / `--history` | Regression vs prior primary beyond similar-band |
| `merge` | Multi-vantage rollup |

Reproduce with the same `--budget`, workload/method, and `--seed` from a similar vantage. URLs in reports are redacted; JSON keeps a hash id, not the key.
