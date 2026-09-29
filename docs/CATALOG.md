# Public catalog

RPCBench ships a **reviewed, versioned** list of keyless public RPCs so you can compare without writing a YAML file:

```bash
pip install rpcbench
rpcbench --help
rpcbench compare --chain ethereum
```

`--chain ethereum` loads the bundled Ethereum mainnet catalog and ranks those endpoints (default **`--budget short`**). Add your own URLs with repeatable **`--endpoint URL`**, or merge a file with **`--endpoints FILE`**.

## Caveats

- **Public RPCs rate-limit.** Expect `rate_limit` / HTTP 429 under load. That is reliability signal, not a crash.
- **Short is the default on catalog runs**, not `long` (Nodeprobe-style “Deep”). Pass `--budget long` only if you accept throttling.
- The catalog is **not** a live Chainlist mirror and **not** an SLA. Prefer your own node or a paid key for production benches.
- Only **keyless HTTPS** endpoints are bundled. No API keys in the package.

## What’s in the package

| Chain | File | Family |
| --- | --- | --- |
| `ethereum` (aliases: `eth`, `mainnet`) | `src/rpcbench/catalogs/ethereum.yaml` | `evm` |

Each YAML has `version`, `reviewed` date, `family`, and a named `endpoints` list (same shape as user endpoint files).

## Source

Endpoints are **hand-reviewed** from public provider docs and [Chainlist](https://chainlist.org) / [DefiLlama chainlist](https://github.com/defillama/chainlist) (`https://chainlist.org/rpcs.json`), then trimmed to keyless HTTPS mainnet URLs that answer basic reads (`eth_blockNumber` / `eth_chainId`). Tracking and keyed demo URLs are skipped.

The catalog **version** increments when membership or URLs change. `reviewed` is the last human pass date.

## Refresh process

1. Pull current public lists (Chainlist `rpcs.json`, provider status pages).
2. Keep only **http(s)** URLs with **no** API-key placeholder.
3. Smoke each candidate: `eth_chainId` → `0x1`, then a short `rpcbench compare --endpoints … --budget short --samples 1`.
4. Drop flaky or key-gated hosts; update `ethereum.yaml` names/URLs.
5. Bump `version`, set `reviewed` to today’s date, note any rate-limit behavior in `notes`.
6. Run unit tests (`tests/test_catalog.py`) and the existing EVM CI smoke (`endpoints.ci.yaml`).
7. Ship in the next PyPI release so `pip install -U rpcbench` picks up the catalog.

Do **not** auto-ingest the full Chainlist dump into the package. Review stays human; the refresh steps above are the process.

## Related flags

| Flag | Role |
| --- | --- |
| `--chain NAME` | Load bundled catalog |
| `--endpoint URL` | Extra URL (repeatable) |
| `--endpoints FILE\|URL` | User file or single URL (optional with `--chain`) |
| `--budget short\|standard\|long` | Sample size; catalog default is `short` |
