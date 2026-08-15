# Boundary: RPCBench vs Nodeprobe vs ValidatorPulse

Three tools, three questions. Do not copy checks across the line.

| Tool | Question | Never does |
| --- | --- | --- |
| **[Nodeprobe](https://github.com/ehsanhajian/nodeprobe)** | Is this RPC **safe to expose**? | Latency percentiles, load, ranking providers |
| **RPCBench** | Which RPC **performs best** for this workload? | Security findings, privileged-namespace probes, TLS/CORS, CVE/client disclosure |
| **[ValidatorPulse](https://github.com/ehsanhajian/ValidatorPulse)** | Is **my validator** healthy? | Scanning other people’s RPCs; comparing providers |

## Nodeprobe owns (RPCBench must not implement)

- Privileged namespace **presence** as a finding: `admin_*`, `personal_*`, `miner_*`, `engine_*`, `txpool_*`, `clique_*`, `eth_accounts`, Solana `validatorExit` / `setLogFilter`, Cosmos unsafe, NEAR adversarial, Starknet devnet, Substrate key injection
- TLS, CORS, `Server` header, `rpc_modules` disclosure, outdated-client / CVE recon
- Security score (0–100), severity (Critical/High/…), escalation (`↳ Next:`)
- Deep **method inventory as attack surface**
- Blocking private/localhost targets (Nodeprobe anti-SSRF). RPCBench **must allow localhost** — you bench your own node
- `--block-providers`, unauthorized-scan warnings as a product feature
- Kill switch path or rule IDs copied from Nodeprobe

## RPCBench owns (even if a method name appears in both)

- Timed samples: P50/P95/P99, jitter, histograms, RPS, batch, load shapes
- Fair paired compare, similar-band, body/hash **consistency** (correctness under load, not “exposed API”)
- Head freshness / lag vs cohort; `latest` vs `safe` vs `finalized` **latency**
- Archive / historical **read performance** (can this indexer finish, and how slow)
- WebSocket **subscribe latency** and missed slots — Nodeprobe is HTTP-only
- Rate limits as **reliability under a budgeted burst**, not “abuse posture”
- Workload coverage: of the methods **this mix needs**, which ones succeeded and how fast
- Optional **trace/debug timing** only when the user opts into a tracing/indexer mix — skip if missing, never a vulnerability
- Client version as a **report label** (interpret Erigon vs Geth results), never a disclosure finding
- HTML/JSON/Prometheus as **benchmark reports**, not finding cards with severity badges

## Shared primitives (OK if the purpose differs)

Identity calls (`eth_chainId`, `getHealth`, `system_health`) to pick a family and confirm the network. Same JSON-RPC, different question.

## Profile names

Nodeprobe: `--profile Quick|Standard|Deep` = **scan budget / escalation**.

RPCBench: `--budget short|standard|long` = **sample count / duration**. Do not reuse Quick/Standard/Deep.

Workload mixes (`general`, `wallet`, `indexer`, `trading`, `nft`, optional `tracing`) are RPCBench-only.
