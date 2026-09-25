# RPCBench

**Which RPC endpoint is fastest — for this call, from this machine?**

A small CLI that compares **EVM, Solana, Substrate, Cosmos, Sui, and NEAR JSON-RPC, plus Aptos REST**: latency, P50/P95/P99, error rate, a ranked table, and JSON. Read-only by default. No accounts. No telemetry. Localhost and RFC1918 are allowed (that is how you bench your own node). Use `--family solana`, `--family substrate`, `--family cosmos`, `--family aptos`, `--family sui`, or `--family near` (or `family:` / `auto` in the endpoints file).

It is **not** a security scanner ([Nodeprobe](https://github.com/ehsanhajian/nodeprobe)) and **not** validator monitoring ([ValidatorPulse](https://github.com/ehsanhajian/ValidatorPulse)). Split: [docs/BOUNDARY.md](https://github.com/ehsanhajian/RPCBench/blob/main/docs/BOUNDARY.md). How the numbers are computed: [docs/METHODOLOGY.md](https://github.com/ehsanhajian/RPCBench/blob/main/docs/METHODOLOGY.md). Roadmap: [issues](https://github.com/ehsanhajian/RPCBench/issues) · epic [#19](https://github.com/ehsanhajian/RPCBench/issues/19).

## Install

```bash
pip install rpcbench
```

Python 3.10+. `rpcbench --version` prints `0.4.0`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

PRs run `pytest`, then a live smoke per family against public RPCs (`eth_blockNumber`, `getSlot`, `chain_getHeader`, `status`, ledger GET, `sui_getLatestCheckpointSequenceNumber`, NEAR `status`; `--samples 1 --warmup 0`). No local node in CI; each family smoke passes if either public endpoint in that file is ok.

## Start

```bash
rpcbench compare --endpoints endpoints.yaml
rpcbench compare --endpoints endpoints.yaml --workload wallet
rpcbench compare --endpoints endpoints.yaml --html -o report.html
```

The first command is the **general** mix at **short** size (verdict and route). `--workload wallet` or `trading` also runs simulate. `--workload indexer` also runs logs-range, archive, and lookback. `--budget` only changes how long to sample. A single call is `--method eth_blockNumber`. Lab flags (`--batch`, `--websocket`, `--rank-by`, …) are in `rpcbench compare --help-all`.

Or a YAML/JSON file of named endpoints (keep API keys in a **local** file; do not commit it):

```yaml
endpoints:
  - name: eth-publicnode
    url: https://ethereum.publicnode.com
    family: evm
  - name: sol-publicnode
    url: https://solana-rpc.publicnode.com
    family: solana
  - name: dot-publicnode
    url: https://polkadot-rpc.publicnode.com
    family: substrate
  - name: atom-publicnode
    url: https://cosmos-rpc.publicnode.com
    family: cosmos
  - name: aptos-labs
    url: https://fullnode.mainnet.aptoslabs.com/v1
    family: aptos
  - name: sui-publicnode
    url: https://sui-rpc.publicnode.com
    family: sui
  - name: near-mainnet
    url: https://rpc.mainnet.near.org
    family: near
```

Compare one family at a time (`--family near` or only that family’s rows in the file). `run` is the same command as `compare`. `rpcbench diff old.json new.json` compares two JSON runs.
## Report

Default is Fastest, a production-readiness **Verdict**, a **Route** (primary / fallback), and the ranked list. `--verbose` is the full dump (including **Signals**). `--json` / `-o` is always the complete payload. `--html -o report.html` is a standalone file (inline CSS/SVG, no CDN): ranking with sample sparklines, a provider × method **Heatmap**, **Signals** (problem / why / next), and print CSS. `--md` is a pasteable GitHub markdown table with aligned columns (ranking, P95, errors, rel, freshness, verdict). `--csv` is one flat row per provider (run, rank, latency, verdict, transport). `-o report.csv` writes that CSV and keeps the CLI table. `rpcbench diff old.json new.json` compares two JSON runs (P95 delta, winner change, new signals) and exits 1 in CI if the previous **primary** got worse beyond the similar-band. `--history DIR` appends a JSON snapshot after a run so diff can use `--history DIR`. Every report prints a **Cite** line (version, git sha, family, vantage, UTC) so the numbers can be reproduced.

**Default**

![Default compact CLI](docs/images/cli-compact.svg)

**`--workload` / `--profile mix`** (Coverage table: which required methods succeeded)

```bash
rpcbench compare --endpoints endpoints.yaml --workload --budget short
rpcbench compare --endpoints endpoints.yaml --workload wallet --budget short
```

![Mix profile compact CLI](docs/images/cli-mix.svg)

**HTTP timing** (`--new-connection --verbose`)

![HTTP timing table](docs/images/cli-timing.svg)

**`--verbose`**

![Verbose CLI](docs/images/cli-verbose.svg)

**HTML report** (`--html -o report.html`) — colored ranking, heatmap, and signals table; works offline; print-ready

![HTML compare report](docs/images/html-report.svg)

**`--profile mix` heatmap** (provider × method; skip/miss is product fit, not a scan)

![HTML mix heatmap](docs/images/html-heatmap.svg)

The default CLI prints, in order:

1. **Summary** — Fastest (P95 by default; similar-band co-winners, not 81ms vs 84ms)
2. **Verdict** — production-ready / risky / not ready for this workload, this run. Labels: `fast+stable`, `slow+reliable`, `similar`, `stale`, `stale-risk`, `timeout`, `rate-limited`, `coverage`, `disagree`, `jitter`, `failed`, `errors`. Not an SLA.
3. **Route** — **primary** and **fallback** among ready endpoints. Stale or disagreeing nodes are never primary. Fallback prefers a different error class (not two 429s). Named when 2+ providers are ready; otherwise fallback is `none`. One paragraph explains the choice.
4. **Ranking** — one table, ordered by `--rank-by`; similar share a place; high error, stale, or disagree is `~`; failed last. **`rel`** is the 0–100 reliability score for this run.
5. **Notes** — one line per endpoint that hit `rate_limit` on timed samples or tags (`merkle  rate_limit=2  tags=2`)
6. **Batch** — when `--batch` is set: support, batch vs serial wall-clock, ratio. `skip` means the extra read did not run (`budget` / `duration`). Not mixed into ranking.
7. **Concurrency** — when `--concurrency` is set: overlapping extra POSTs vs the same N serial (per-request P50/P95, ratio, error count). Default is off. Not mixed into ranking.
8. **Throughput** — when `--throughput` is set: extra serial POSTs, successful req/s, duration, and completed count. `--rps` caps starts. 429 is a rejected request, not a crash. Default is off. Not mixed into ranking.
9. **Logs range** — when `--logs-range` is set: pinned `eth_getLogs` at 1 / 10 / 100 / up to N blocks (latency, bytes, n, truncation). Mix logs stay one block. A provider that only dies at 1000 blocks shows here. Not mixed into ranking.
10. **Archive** — when `--archive` is set: `eth_getBalance` at genesis, classified yes / no / unknown / rate-limited. Missing archive is a capability result, not a crash. Not mixed into ranking.
11. **History** — when `--lookback N` is set: timed `eth_getBalance` at pin−N vs latest (latency, error rate, ratio). Endpoints without archive/history skip with a reason. Not mixed into ranking.
12. **WebSocket** — when `--websocket` is set: connect time, `eth_subscribe` `newHeads` latency, first-event delivery, missed heads, and disconnects over a bounded window. Missing `ws`/`websocket` URL is **not configured**. Not mixed into ranking.

`--verbose` adds the rest (same numbers, no data loss):

13. **Comparison** — YAML order (failed rows stay in place; head / lag / fresh / hash / match; **rel**)
14. **Reliability** — breakdown of `rel` (errors, timeouts, tail, mix coverage). Not an SLA. Not a security score.
15. **Signals** — each problem / why / next (routing and config: raise `--timeout`, pick another endpoint, pin `--block`). Not CVE language, not hardening.
16. **Coverage** — active mix only: each required method is `ok`, an error class, or `skip` if not offered (`skip/unsupported` / `skip/restricted` / `skip/timeout` for optional trace/simulate). A miss is product fit (indexer `eth_getLogs` 404s), not a vuln. Compact `--workload` / `--profile mix` prints this table; JSON is `coverage`.
17. **Methods** — per-method P50/P95/P99 and errors when a mix is active (ranking still uses the whole mix)
18. **Timing** — handshake (DNS+TCP+TLS) vs server wait vs payload (body+parse). Not mixed into ranking. Default is keep-alive; `--new-connection` is a cold handshake every request
19. **Transport** — negotiated HTTP proto (`1.1` / `2`), content-encoding, request/response bytes. Size vs latency is in HTML (log bytes, colored by method). Not mixed into ranking. `--http2` asks for HTTP/2; `--http1` forces 1.1
20. **Tags** — one paired `latest` / `safe` / `finalized` snapshot (skipped with a reason if the tag is missing)
21. **Burst** — burst vs steady error rate and recovered rps when `--burst` is set (same request budget). Extra tag 429s show as `tags=N`, not in timed `n`/`err`.
22. **Providers** — one table: redacted URL, client, n/err, p95, head/lag/fresh/match, histogram (`≥1s=3`), note. Per-sample rows follow.
23. **Capabilities** — who answered this method (and whether batch was supported, when enabled)

On a TTY, ok is green and fail is red (`NO_COLOR` or a pipe turns color off). Reports never print API keys, bearer tokens, or header values.

## How a run works

Numbers and caveats: [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

### Workload

- **Paired by default:** one shared read-only sequence; each sample is raced to every provider at the same time. `--sequential` is A-then-B.
- **`--budget`** picks a named size (`short` / `standard` / `long`). That sets how many mix rounds to take. **`--max-requests`** is the HTTP cap (how many requests the run may send). `--samples` and `--warmup` override the named size. `long` is more samples only — not archive, WebSocket, or tracing unless the workload asks.
- **`--workload general|wallet|indexer|trading|nft|tracing`** runs a documented, weighted, read-only mix. Omit the name for **general**. **`--profile mix`** is the same as `--workload general`. `--samples` is per mix round; a step’s weight is how often it appears in that round. Ranking uses the whole mix, not one cheap head read. **Coverage** is those methods only: `ok`, error class, or `skip` if not offered. A failing `eth_getLogs` is a miss for `--workload indexer`, not a vuln; `--workload wallet` does not send logs and emphasizes `eth_getBalance` / `eth_call` / `eth_estimateGas`. **`--workload tracing`** times optional `trace_block` and `debug_traceCall`; missing traces skip, not a crash. Payloads and weights: [docs/METHODOLOGY.md](docs/METHODOLOGY.md).
- **`--simulate`** adds read-only `eth_call`, `eth_estimateGas`, and `eth_simulateV1` (fixture tx, never a send). **wallet** and **trading** turn this on. `--no-simulate` turns it off. Missing simulateV1 is skip, not a crash, and does not tank ranking. Details: [Read-only simulation](docs/METHODOLOGY.md#read-only-simulation).
- **`--profile FILE.yaml`** is a custom mix you write (methods, weights, optional timeout/notes). `source: latest_head|recent_block|known_contract|seeded_address` fills params from a shared chain snapshot and `--seed` so every provider gets the same sequence. If the chain cannot supply data, documented fixtures (`latest`, zero address) are used. Full YAML example, sources, seed, and fallback: [Custom YAML profiles](docs/METHODOLOGY.md#custom-yaml-profiles).
- **Burst** is opt-in (`--burst N`, max 8). The first N timed samples overlap; the rest are a steady phase, optionally capped with `--rps`. Burst splits the existing sample budget and does not add requests. Burst vs steady error rate and rps are reported separately. Tag 429s are `tags=N` on that table (not mixed into timed n/err). Default is off (`--burst 0`, `--rps 0`). Ramp/spike/soak shapes are a later issue.
- **Concurrency** is opt-in (`--concurrency N`, omit N for 4, max 8). After timed samples, RPCBench sends N overlapping HTTP POSTs of the primary method, then the same N calls one-by-one, and reports per-request concurrent P50/P95 vs serial P50 plus the concurrent error count. Adds 2N requests per endpoint. Default is off (`--concurrency 0`). Not an unbounded load test.
- **Throughput** is opt-in (`--throughput N`, omit N for 20, max 64). After timed samples, RPCBench sends N extra serial POSTs of the primary method and reports successful req/s, wall duration, and completed count. `--rps` caps how often those starts fire. HTTP 429 is a rejected request (`rate_limit`), not a crash. Adds N requests per endpoint. Default is off (`--throughput 0`). Not concurrent fan-out and not an unbounded load test.
- **Batch** is opt-in (`--batch N`, omit N for 3, max 8). After timed samples, RPCBench sends one JSON-RPC array of N copies of the primary method, then the same N calls one-by-one, and reports wall-clock and the serial/batch ratio. A single-object error means the provider does not support batch (capability, not a crash). Partial item errors are marked `partial`. Adds 1+N requests per endpoint. Default is off (`--batch 0`). Not HTTP/2 multiplexing.
- **Logs range** is on for **indexer** (1000 blocks) and otherwise opt-in (`--logs-range N`, omit N for 1000, `0` off). After the pin, the same zero-address `eth_getLogs` is sent at 1, 10, 100, and up to N blocks. Mix logs stay `latest→latest`. Too-short chains skip with `head`. Truncation and 1000-only failures show in the Logs range table. Adds up to 4 requests per endpoint.
- **`--archive`** probes historical state with one `eth_getBalance` of the zero address at genesis. **indexer** turns this on. `--no-archive` turns it off. Classified yes / no / unknown / rate-limited. Chains shorter than 128 blocks skip with `head`. Missing archive is a capability result, not a crash, and does not change ranking. Details: [Archive / historical state](docs/METHODOLOGY.md#archive--historical-state).
- **`--lookback N`** times `eth_getBalance` at `pin−N` vs `latest` (indexer uses 1000; otherwise omit N for 1000, `0` off). Reuses `--archive` to skip when the node has no history. Not mixed into ranking. Details: [Historical queries](docs/METHODOLOGY.md#historical-queries).
- **`--websocket SEC`** times WebSocket connect, `eth_subscribe` `newHeads`, and first-event delivery over a bounded window (omit SEC for 3s, max 10s). Needs an optional `ws` / `websocket` URL on the endpoint (`ws://` or `wss://`). Missing WS is **not configured**. Disconnects and missed block-number gaps are counted in that window. Does not consume `--max-requests`. Not mixed into ranking. Details: [WebSocket subscribe](docs/METHODOLOGY.md#websocket-subscribe).

### Stats

- **Warmup is excluded** from min/mean/max, jitter, percentiles, error rate, and the histogram.
- **P50/P95/P99** are nearest-rank over successful samples. **Jitter** is the sample standard deviation of those samples (needs n≥2). **P99** is the slowest sample until n≥100 (flagged below that).
- **Histogram** is where successes landed: empty buckets are omitted (`≥1s=3`, or `<50ms=8  ≥1s=8` when split). Buckets: `<50ms`, `<100ms`, `<250ms`, `<1s`, `≥1s` (same edges in JSON).
- **HTTP timing** splits each successful sample into handshake (DNS+TCP+TLS), server wait after the connection is ready, and payload (body download + JSON parse). Ranking still uses total RTT. Default reuses keep-alive connections (handshake is ~0 after warmup). `--new-connection` opens a fresh TCP/TLS session every request so distance vs node time is visible. TLS here is handshake latency, not a certificate check.
- **HTTP transport** records the negotiated protocol (`1.1` or `2`), `Content-Encoding` (`gzip`, `br`, …), request bytes, and response wire bytes. Huge `eth_getLogs` payloads and missing compression look like slow nodes. HTML **Size vs latency** is log(bytes) vs RTT, colored by method, so a 40-byte head and a 7kB `getBlock` are not stacked on the Y-axis. Ranking still uses total RTT. Default is HTTP/1.1. `--http2` asks for HTTP/2 via ALPN (falls back to 1.1). `--http1` forces HTTP/1.1. Not a TLS or CORS check.
- **Error rate** is failed/attempted, with a class (timeout, connection, HTTP 4xx/5xx, **rate_limit**, JSON-RPC, malformed). `rate_limit` is HTTP 429 or a CU/throttle JSON-RPC message — reliability, not a scan.
- **rps** in the ranking table is `1000 / mean_ms` for this probe — not parallel throughput. **`--throughput N`** is a bounded extra-read of successful req/s over a serial window (duration and completed count in that table). `--rps N` caps starts after `--burst` and during `--throughput`, not that formula.
- **Reliability `rel`** is 0–100 for **this run** (not an SLA, not a security score). Same samples always produce the same score:

  `rel = round( 50×(1−error_rate) + 20×(1−timeout_share) + 20×(1−tail) + 10×coverage )`

  `timeout_share` is timeouts / attempted. `tail` is 0 when P99=P50 and 1 when P99/P50 ≥ 3. `coverage` is the fraction of mix steps with at least one success (1.0 for a single method that answered). A 100% error run is **0**. A clean run with a flat tail is **100**. `--verbose` prints the four parts. JSON is `reliability` (score plus breakdown).

### Ranking

- Default is P95 of successes (over the mix when `--workload` / `--profile mix`). Override with `--rank-by p50|p95|p99|mean|rps` (`throughput` = `rps`). Lower latency wins; higher rps wins.
- **Similar-band** (default 10%) shares a place when the worse value is within that fraction of the better. Error rate above the same band, a **stale** head, a **disagreeing** block hash, or a **coverage miss** (a required mix step never succeeded) is not a numbered place (`~`). Failed (`n_ok=0`) never take Fastest.
- **Route** names a **primary** and **fallback** among **ready** endpoints (never stale or disagree). Primary is the best reliability score within the similar-band of the fastest ready node, preferring known freshness and a matching hash. Fallback is the next ready endpoint; if primary had a timed error class, fallback skips others with that same class when a diverse ready alternative exists. One paragraph in the compact CLI explains the choice. JSON is `route`.

### Extra reads

These are not mixed into latency stats or Fastest.

- **Freshness** is lag vs the cohort’s upper-median `eth_blockNumber` in the same window. Default `--stale-blocks 2`. Tables print **yes** when lag is within that tolerance, **stale** when it exceeds it (Ranking note may add `~Ns`). Lag time uses `--block-time` or a known chain from `eth_chainId` already in the mix (12s on Ethereum). Extra head reads happen only when the workload has no `eth_blockNumber`. JSON still uses `fresh` / `stale`.
- **Consistency** is whether providers return the same block **hash** at one pinned height (default: that cohort median). `--block HEX|N` pins the check when heads naturally diverge by one block. A unique majority hash is canonical; a split is disagreement for everyone who returned a hash. Tables print **yes** / **no** under match. Missing/unparseable hashes are unknown, not disagree. JSON still uses `agree` / `disagree`. Not fork choice and not a security finding.
- **Client** is a volunteered `web3_clientVersion` string stored as a label (Erigon vs Geth). Missing or hex-only results are omitted. Not a disclosure finding, not outdated-client recon, not a CVE check.
- **Tags** are one paired `eth_getBlockByNumber` snapshot each for `latest`, `safe`, and `finalized`. Latency and freshness are per tag vs that tag’s cohort. Unsupported tags are skipped with a reason and do not change Fastest. Full P95 of one tag is `--method eth_getBlockByNumber --params '["finalized", false]'`.
- **Batch** is one JSON-RPC array of N calls vs the same N sent serially (`--batch N`). Wall-clock and ratio are extra reads. Unsupported batch is `batch_unsupported`; partial item errors are `partial`. Not mixed into Fastest.
- **Concurrency** is N overlapping HTTP POSTs of the primary method vs the same N serial (`--concurrency N`). Per-request P50/P95, ratio vs serial, and concurrent error count. Bounded extra read (max 8), not a load generator. Not mixed into Fastest.
- **Throughput** is N extra serial POSTs of the primary method (`--throughput N`). Successful req/s, duration, and completed count. `--rps` caps starts. 429 is rejected, not a crash. Bounded extra read (max 64), not concurrent fan-out. Not mixed into Fastest.
- **Logs range** is opt-in (`--logs-range`, omit N for 1000). After the pin is known, RPCBench sends the same `eth_getLogs` (zero address, no topics) at 1, 10, 100, and up to N blocks ending at that pin. Mix catalogs still use `latest→latest`. A range is skipped with reason `head` when the chain is shorter than N blocks. Truncation (result cap / “too many logs”) is a table cell, not a ranking change. Adds up to 4 requests per endpoint.
- **Archive** is opt-in (`--archive`). After the pin is known, one `eth_getBalance` of the zero address at genesis classifies the endpoint yes / no / unknown / rate-limited. Too-short chains skip with `head`. Not mixed into ranking. Adds 1 request per endpoint.
- **History** is opt-in (`--lookback N`, omit N for 1000). Timed `eth_getBalance` at pin−N vs latest. `--lookback` turns on archive detection; a pruned node skips with `archive`. Not mixed into ranking. Adds up to 2 requests per endpoint.
- **WebSocket** is opt-in (`--websocket SEC`, omit SEC for 3s, max 10s). Connect + `eth_subscribe` `newHeads` on the optional `ws` / `websocket` URL. Missing WS is **not configured**. Reports connect, subscribe, first-event, missed heads, and disconnects. Does not consume `--max-requests`. Not mixed into ranking.

### Capture and replay

`rpcbench record` writes a JSONL capture (one `method` + `params` object per line) from the same mix `run` would send. `rpcbench replay --from FILE` sends that sequence in **lockstep** and compares canonicalized bodies among who answered. A down or 429 node is status/error on that row, not a body mismatch. `--verbose` prints a unified diff when bodies disagree. Write methods (`eth_send*`, `personal_*`, …) are blocked unless `--allow-writes`. Not mixed into ranking. Details: [Traffic capture and replay](docs/METHODOLOGY.md#traffic-capture-and-replay).

### JSON

`--json` or `-o FILE` includes `mode`, `seed`, `sequence_id`, `connection` (`keepalive` or `new`), `http` (`1.1` or `2`), a `watermark` (version, git sha, UTC, budget, workload, seed, family, vantage, sample counts, plus [methodology](docs/METHODOLOGY.md) and [boundary](docs/BOUNDARY.md) URLs), `coverage` (active mix steps only), `reliability` (0–100 this-run score plus breakdown; not success rate alone), `verdict` (ready / risky / not_ready plus `kind` and problem/why/next `signals`), `route` (primary / fallback / why), per-provider `id` (URL fingerprint, not printed in the CLI table), per-sample `pairs` (body hashes), `jitter_ms`, `histogram`, `freshness`, `consistency`, `client`, `tags`, `burst`, `batch` (size, supported, wall-clock vs serial), `inflight` (overlapping extra POSTs vs serial: percentiles, ratio, errors), `throughput` (serial extra-read: successful req/s, duration, completed count), `logs_range` (pinned getLogs windows: latency, bytes, n, truncation), `archive` (genesis-state yes / no / unknown / rate-limited), `history` (pin−N vs latest latency, error rate, ratio), `websocket` (connect / subscribe / first-event ms, n, missed heads, disconnects), HTTP `timing` percentiles, `transport` (proto, encoding, bytes), and burst `phases`.

`--md` is GitHub-flavored markdown with the same ranking numbers as JSON (P95, err, rel, fresh, match, verdict), plus Transport, Batch, Concurrency, Throughput, Logs range, Simulation, Trace, Debug, Archive, History, and WebSocket tables when that run measured them. `--csv` is one row per provider with the same ranking, verdict, transport, batch, inflight, throughput, logs-range, simulate, trace, debug, archive, history, and websocket fields. `rpcbench diff` reads two of these JSON files. Not a security finding.

## Flags

Happy path is `compare --endpoints FILE` (general, short). Named jobs turn extras on: wallet and trading add simulate; indexer adds logs-range, archive, and lookback. `--budget long` does not. Overrides (`--no-simulate`, `--no-archive`, `--logs-range 0`, `--lookback 0`, `--websocket 0`) and the rest of the lab flags are `rpcbench compare --help-all`. `--profile FILE.yaml` is a custom mix. `--preset`, `--profile mix`, and `run` still work.

`--budget` is a **named size** (how long to sample). `--max-requests` is the **HTTP cap**. `--samples` / `--warmup` override the named size.

| `--budget` | Samples | Warmup | Timeout | Stop after |
| --- | --- | --- | --- | --- |
| `short` | 3 | 0 | 5s | 30s |
| `standard` | 10 | 1 | 10s | 600s |
| `long` | 50 | 2 | 15s | 1800s |

| Flag | Default | |
| --- | --- | --- |
| `--budget` | `short` with no other flags; else `standard` | Named size in the table above. Does not enable archive, WebSocket, or tracing |
| `--samples` | 10 | Timed mix rounds after warmup (overrides `--budget`). Each round sends `sum(weights)` calls |
| `--warmup` | 1 | Requests excluded from stats (overrides `--budget`) |
| `--timeout` | 10s | Per-request timeout (overrides `--budget`) |
| `--max-requests` | 128 | HTTP cap for the whole run (hard cap `RPCBENCH_MAX_REQUESTS`, default 10000) |
| `--max-duration` | 600s | Stop and still print a report (overrides `--budget`; `0` = no limit) |
| `--concurrency` | 0 | Overlap N extra POSTs vs N serial (`0`=off, omit N for 4, max 8). Extra 2N requests/endpoint. Not mixed into ranking |
| `--burst` | 0 | Overlap the first N timed samples (`0`=off, max 8). Same request budget |
| `--rps` | 0 | Cap starts/sec after `--burst` and during `--throughput` (`0`=off). Does not raise the budget |
| `--throughput` | 0 | Extra serial POSTs for successful req/s (`0`=off, omit N for 20, max 64). Extra N requests/endpoint. 429 is rejected |
| `--batch` | 0 | JSON-RPC batch of N vs N serial (`0`=off, omit N for 3, max 8). Extra 1+N requests/endpoint |
| `--logs-range` | 0 (1000 on indexer) | Pinned `eth_getLogs` at 1/10/100/up to N blocks (`0`=off, omit N for 1000). Mix logs stay 1 block |
| `--simulate` | on for wallet and trading | Add read-only `eth_call` / `eth_estimateGas` / `eth_simulateV1`. `--no-simulate` turns it off. Missing simulateV1 is skip |
| `--archive` | on for indexer | Probe `eth_getBalance` at genesis. yes / no / unknown / rate-limited. `--no-archive` turns it off. Not mixed into ranking |
| `--lookback` | 0 (1000 on indexer) | Timed `eth_getBalance` at pin−N vs latest (`0`=off, omit N for 1000). Skips without archive |
| `--websocket` | 0 | Connect + `eth_subscribe` `newHeads` (`0`=off, omit SEC for 3s, max 10s). Missing WS is not configured |
| `--new-connection` | off | Fresh TCP/TLS every request. Default is keep-alive |
| `--http2` | off | Prefer HTTP/2 via ALPN (falls back to 1.1). Not mixed into ranking |
| `--http1` | off | Force HTTP/1.1 (default) |
| `--seed` | 0 | Shared sequence stamp. YAML `source` picks (recent block, seeded address) use this |
| `--rank-by` | `p95` | `p50`, `p95`, `p99`, `mean`, or `rps` |
| `--similar-band` | `0.10` | Relative band on the rank key (10%). High error above this is `~`, not a place |
| `--stale-blocks` | `2` | Head lag (blocks vs cohort median) above this is stale. Set per chain |
| `--block-time` | `12` or known chain | Seconds per block for estimated lag time |
| `--block` | cohort median | Pin the head-hash check (`hex`, decimal, or `latest`) |
| `--preset` | | `head` (`eth_blockNumber`), `chainId`, or `balance` (`eth_getBalance` of the zero address) |
| `--family` | `evm` | Benchmark family: `evm`, `solana`, `substrate`, `cosmos`, `aptos`, `sui`, `near`, or `auto` (`eth_chainId` / `getHealth` / `system_health` / `status` / sui checkpoint / `network_info` / ledger GET). Other families error. Not a scan |
| `--workload` | `general` when omitted | `wallet`, `indexer`, `trading`, `nft`, `tracing`. Weighted mix. Omit the flag for general + short. Do not combine with `--method` or `--preset` |
| `--profile` | | YAML mix file, or alias `mix` = general. Schema: [Custom YAML profiles](docs/METHODOLOGY.md#custom-yaml-profiles) |
| `--method` / `--params` | | Single JSON-RPC method and JSON array of params. Head probe: `--method eth_blockNumber`. Do not combine `--method` with `--preset` |
| `--allow-writes` | off | Required for write methods (`eth_send*`, `personal_*`, …) |
| `--verbose` | off | Full CLI report (Comparison, Reliability, Signals, Coverage, Timing, Tags, Burst, Providers, per-sample). Batch, Concurrency, Throughput, Logs range, simulate Methods, Archive, History, and WebSocket are already in the compact report when those flags are set. Replay prints body diffs |
| `--json` / `-o FILE` | | JSON to stdout, and/or write JSON to a file (table still prints unless `--json`, `--md`, or `--csv`) |
| `--html` | off | Standalone HTML to `-o FILE` (inline CSS/SVG, heatmap, signals, print CSS). Table still prints unless `--json` |
| `--md` | off | GitHub-flavored markdown (ranking + match; Transport, Batch, Concurrency, Throughput, Logs range, Archive, History, Trace, Debug, WebSocket when measured). `-o FILE` writes the same markdown |
| `--csv` | off | Flat CSV (one row per provider; run, rank, latency, verdict, transport, batch, inflight, throughput, logs-range, archive, history, trace, debug, websocket). `-o FILE` or `-o report.csv` writes CSV |
| `--history DIR` | | Append a JSON snapshot to DIR after the run |
| `--sequential` | off | Run endpoints back-to-back instead of paired |
| `record -o FILE` | | Write a JSONL capture of the mix (`method` + `params` per line) |
| `replay --from FILE` | | Lockstep replay; compare status, JSON-RPC error, and canonicalized body |

## Safety

Kill switch: `RPCBENCH_DISABLED=1`, or create `~/.config/rpcbench/DISABLED` (override path with `RPCBENCH_DISABLE_FILE`). RPCBench never prompts for a private key. Set `RPCBENCH_VANTAGE` to label the machine in the report watermark (default: hostname).

## License

[MIT](https://github.com/ehsanhajian/RPCBench/blob/main/LICENSE)
