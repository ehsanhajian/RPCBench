# For node operators: pick a primary and a fallback RPC from your VPS

Published as part of RPCBench discoverability (#66). Paste or link this when posting to EthStaker / Solana RPC / operator chats.

---

**TL;DR:** [`rpcbench`](https://github.com/ehsanhajian/RPCBench) is a small local CLI that answers *which RPC is fastest for this workload from this machine* — latency, P50/P95/P99, errors, freshness — without a SaaS account.

```bash
pip install rpcbench
rpcbench compare --endpoints endpoints.yaml
# on a VPS with a browser UI:
rpcbench compare --endpoints endpoints.yaml --web --web-host 0.0.0.0
```

Landing: https://ehsanhajian.github.io/RPCBench/  
PyPI: https://pypi.org/project/rpcbench/

It is **not** a security scanner ([Nodeprobe](https://github.com/ehsanhajian/nodeprobe)) and **not** validator monitoring ([ValidatorPulse](https://github.com/ehsanhajian/ValidatorPulse)). You bring your own endpoints; no public catalog.

Demo (CLI): https://ehsanhajian.github.io/RPCBench/images/cli-compact.svg
