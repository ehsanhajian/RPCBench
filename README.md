# RPCBench

**Which RPC endpoint performs best — for this workload, from this region?**

RPCBench is a vendor-neutral CLI that measures RPC **quality** and compares providers: latency tails, freshness, integrity, method compatibility, rate limits, and a production-readiness verdict. It recommends a primary and a fallback. It does not guess; it publishes the methodology.

**Non-goals:** security auditing ([Nodeprobe](https://github.com/ehsanhajian/nodeprobe)) and always-on validator monitoring ([ValidatorPulse](https://github.com/ehsanhajian/ValidatorPulse)). Read-only by default. No accounts. No telemetry.

How the three tools split: [docs/BOUNDARY.md](docs/BOUNDARY.md). RPCBench never probes privileged namespaces, TLS/CORS, or client disclosure. It allows localhost (you are benchmarking your own node).

## What it measures

| Area | Signals |
| --- | --- |
| **Performance** | Latency, P50/P95/P99, jitter, histogram, DNS/TLS/TTFB split, HTTP/2, throughput, batch, concurrency, load shapes |
| **Workload** | General / wallet / indexer / trading / NFT mixes, custom YAML, on-chain payloads, capture/replay, getLogs ranges, simulation |
| **Capability** | Archive/history performance, WS subscribe latency, workload coverage, latest/safe/finalized, optional trace timing |
| **Honesty** | Paired interleaved compare, similar-band, body consistency, vantage/region, rate-limit reliability, head lag |
| **Decision** | Reliability score, ranking, production verdict, performance signals, primary + fallback |
| **Output** | CLI, live TUI, local web UI, HTML (charts/heatmap), JSON, CSV, Markdown, Prometheus, Grafana |
| **Families** | EVM (all chain IDs), Solana (+ gRPC first-seen), Substrate, Cosmos, Aptos, Sui, NEAR, Starknet, Bitcoin, TON |

Implementation is the [issue tracker](https://github.com/ehsanhajian/RPCBench/issues) — complete product, not an MVP slice. Parent epic: [#19](https://github.com/ehsanhajian/RPCBench/issues/19).

## Planned usage

```bash
rpcbench compare --chain ethereum
rpcbench compare --endpoints endpoints.yaml --workload indexer --budget standard --html -o report.html
rpcbench replay --from capture.jsonl --endpoints endpoints.yaml
```

## License

[MIT](LICENSE)
