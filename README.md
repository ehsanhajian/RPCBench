# RPCBench

**Which RPC endpoint performs best?**

RPCBench measures RPC quality and compares providers. It answers that question with latency percentiles, throughput, error rates, capability probes, and a reliability score — from your terminal.

**Non-goal:** it does not perform security auditing. Use [Nodeprobe](https://github.com/ehsanhajian/nodeprobe) for RPC surface and security scans.

## Status

Planning. Implementation is tracked in [GitHub issues](https://github.com/ehsanhajian/RPCBench/issues).

## What it measures

| Area | Signals |
| --- | --- |
| **Performance** | Latency, P50/P95/P99, throughput, concurrent requests, error rate |
| **Capability** | Archive support, Trace API, Debug API, WebSocket performance, historical queries |
| **Comparison** | Multi-provider runs, performance ranking, reliability score |
| **Output** | CLI reports, JSON, CSV, Prometheus metrics |

## Planned usage

```bash
rpcbench run --endpoints endpoints.yaml
rpcbench compare --endpoints endpoints.yaml --json
```

## License

[PolyForm Noncommercial License 1.0.0](LICENSE)
