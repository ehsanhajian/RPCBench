# Operator post (paste template)

Short copy for EthStaker / Solana RPC / operator chats. Landing: https://ehsanhajian.github.io/RPCBench/

---

**TL;DR:** [RPCBench](https://github.com/ehsanhajian/RPCBench) is a local CLI that answers *which RPC is fastest for this workload from this machine* — latency, P50/P95/P99, errors, freshness — with no SaaS account.

```bash
pip install rpcbench
rpcbench compare --endpoints endpoints.yaml

# VPS + browser UI (no auth — open the port only when you mean to):
rpcbench compare --endpoints endpoints.yaml --web --web-host 0.0.0.0
```

- Site: https://ehsanhajian.github.io/RPCBench/
- PyPI: https://pypi.org/project/rpcbench/
- Demo: https://ehsanhajian.github.io/RPCBench/images/cli-compact.svg

Not a security scanner ([Nodeprobe](https://github.com/ehsanhajian/nodeprobe)). Not validator monitoring ([ValidatorPulse](https://github.com/ehsanhajian/ValidatorPulse)). You bring your own endpoints.
