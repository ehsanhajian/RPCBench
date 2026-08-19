# RPCBench

**Which RPC endpoint is fastest — for this call, from this machine?**

RPCBench 0.1 is a small CLI that compares **EVM JSON-RPC over HTTP**: round-trip latency, P50/P95/P99, error rate, a ranked table, and JSON. Read-only by default. No accounts. No telemetry. Localhost and RFC1918 are allowed (that is how you bench your own node).

It is **not** a security scanner ([Nodeprobe](https://github.com/ehsanhajian/nodeprobe)) and **not** validator monitoring ([ValidatorPulse](https://github.com/ehsanhajian/ValidatorPulse)). How the three tools split: [docs/BOUNDARY.md](https://github.com/ehsanhajian/RPCBench/blob/main/docs/BOUNDARY.md).

The longer product (other families, workload mixes, HTML/TUI, production verdict) lives in the [issue tracker](https://github.com/ehsanhajian/RPCBench/issues). Parent epic: [#19](https://github.com/ehsanhajian/RPCBench/issues/19).

## Install

```bash
pip install rpcbench
```

Requires Python 3.10+. `rpcbench --version` prints `0.1.0`.

From a clone (contributors):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Pull requests and pushes to `main` run `pytest`, then a live smoke: `rpcbench run` against PublicNode and dRPC (`--samples 1 --warmup 0`). There is no local node in CI. The smoke passes if either public endpoint is ok.

## Quick start

```bash
rpcbench compare --endpoints https://ethereum.publicnode.com --samples 5 --warmup 1
```

Or a YAML/JSON file of named endpoints (keep API keys in a **local** file; do not commit it):

```yaml
endpoints:
  - name: publicnode
    url: https://ethereum.publicnode.com
  - name: drpc
    url: https://eth.drpc.org
  - name: paid
    url: https://eth.example/v3/YOUR_KEY
    bearer: YOUR_TOKEN
    headers:
      X-Api-Key: YOUR_KEY
```

```bash
rpcbench run --endpoints endpoints.yaml
rpcbench compare --endpoints endpoints.yaml --json
rpcbench run --endpoints endpoints.yaml -o report.json
```

`run` and `compare` are the same command. They print **summary**, **ranking**, **per-provider metrics**, and **method coverage**. Ranking is by **mean of successful samples** (warmup excluded); failed endpoints are last; ties keep config order. On a TTY, ok is green and fail is red (`NO_COLOR` or a pipe disables this). Reports print a redacted URL plus a short hash (`id=`), never API keys, bearer tokens, or header values.

### Flags

```bash
rpcbench run --endpoints endpoints.yaml --samples 10 --warmup 1 --preset head --timeout 10 --budget 128
rpcbench compare --endpoints http://127.0.0.1:8545
rpcbench run --endpoints endpoints.yaml --verbose --json
```

`--samples` timed requests per endpoint after `--warmup` (defaults: 10 and 1). Warmup is excluded from min/mean/max, percentiles, and error rate. P50/P95/P99 are nearest-rank over successful samples. Error rate is failed/attempted with a class breakdown (timeout, connection, HTTP 4xx/5xx, JSON-RPC, malformed). `--verbose` prints each sample. `--preset` is `head` (`eth_blockNumber`), `chainId`, or `balance` (`eth_getBalance` of the zero address). Or pass `--method` and optional `--params` (JSON array). Write methods are rejected unless `--allow-writes`.

`--json` prints a machine-readable report to stdout instead of the table. `-o FILE` writes that JSON to a file (the table still prints unless you also pass `--json`). Sequential `rps` is `1000 / mean_ms`. Reliability `score` is success rate.

`--budget` is the max HTTP requests for the whole run, including warmup (default 128, hard cap `RPCBENCH_MAX_REQUESTS` default 10000). `--max-duration` stops remaining work after N seconds and still prints the report (default 600; `0` = no limit). `--concurrency` is 1.

Kill switch: set `RPCBENCH_DISABLED=1`, or create `~/.config/rpcbench/DISABLED` (override path with `RPCBENCH_DISABLE_FILE`). RPCBench never prompts for a private key.

## License

[MIT](https://github.com/ehsanhajian/RPCBench/blob/main/LICENSE)
