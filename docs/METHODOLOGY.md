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

An endpoint whose **error rate is above the same band** does not get a numbered place or Fastest. It is listed after placed rows as `~`. Failed endpoints (`n_ok=0`) stay last.

We use this documented band instead of bootstrap confidence intervals. Typical `--samples 10` is too small for a stable P95 CI.

## P99

Nearest-rank P99 is the **slowest success** until **n ≥ 100**. Below that it is flagged (`p99_reliable: false`). Default `--samples 10` is not enough for P99.

## Jitter and histogram

Jitter is the sample standard deviation (needs n≥2). Histogram buckets: `<50ms`, `<100ms`, `<250ms`, `<1s`, `≥1s`.

## Non-claims

Not an SLA. Not a security audit. Not geographic unless you run from more than one machine. Sequential `rps` is `1000 / mean_ms`, not parallel throughput.
