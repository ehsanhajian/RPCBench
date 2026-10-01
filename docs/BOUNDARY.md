# Boundary: RPCBench vs Nodeprobe vs ValidatorPulse

Three tools, three questions. Do not copy checks across the line.

| Tool | Question | Never does |
| --- | --- | --- |
| **[Nodeprobe](https://github.com/ehsanhajian/nodeprobe)** | Is this RPC **safe to expose**? | Latency percentiles, load tests, provider ranking |
| **RPCBench** | Which RPC **performs best** for this workload? | Security findings, privileged probes, TLS/CORS, CVE / client disclosure |
| **[ValidatorPulse](https://github.com/ehsanhajian/ValidatorPulse)** | Is **my validator** healthy? | Scanning other people’s RPCs; comparing providers |

## Nodeprobe owns (RPCBench must not)

- Privileged namespace **presence** as a finding (`admin_*`, `personal_*`, `miner_*`, `engine_*`, `txpool_*`, `clique_*`, `eth_accounts`, Solana `validatorExit` / `setLogFilter`, Cosmos unsafe, NEAR adversarial, Starknet devnet, Substrate key injection)
- TLS, CORS, `Server` header, `rpc_modules` disclosure, outdated-client / CVE recon
- Security score, severity badges, escalation copy
- Deep method inventory as **attack surface**
- Blocking localhost / private IPs (Nodeprobe anti-SSRF). RPCBench **allows localhost** so you can bench your own node
- `--block-providers`, unauthorized-scan warnings as a product feature
- Kill-switch paths or rule IDs copied from Nodeprobe

## RPCBench owns

- Timed samples: P50/P95/P99, jitter, histograms, RPS, batch / concurrency / throughput extras, load shapes
- Fair paired compare, similar-band ranking, body/hash **consistency** (correctness under load — not “exposed API”)
- Capture / replay lockstep integrity (status, error class, canonical body)
- Head freshness / lag; `latest` / `safe` / `finalized` **latency**
- Archive and historical **read performance**
- WebSocket subscribe latency; Solana Yellowstone/gRPC first-seen race
- Rate limits as **reliability under a budgeted burst**
- Workload coverage: which methods **this mix** needs succeeded, and how fast
- Optional trace/debug **timing** when opted in — skip if missing, never a vulnerability
- Client version as a **label**, never a disclosure finding
- HTML / JSON / Prometheus / Grafana as **benchmark reports** (no severity cards)
- Docker image + GitHub Action with SLO exit codes

## Shared primitives

Identity handshakes (`eth_chainId`, `getHealth`, `system_health`, `status`, sui checkpoint, `network_info`, `starknet_blockNumber`, `getblockchaininfo`, `getMasterchainInfo`, ledger GET) to pick a family. Same JSON-RPC, different question.

## Profile names

| Nodeprobe | RPCBench |
| --- | --- |
| `--profile Quick\|Standard\|Deep` = scan budget | `--budget short\|standard\|long` = sample count / duration |

Do not reuse Quick / Standard / Deep. Workload mixes (`general`, `wallet`, `indexer`, `trading`, `nft`, `tracing`) are RPCBench-only.
