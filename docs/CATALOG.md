# Public catalog (live Chainlist)

`--chain ethereum` fetches public RPCs from [Chainlist](https://chainlist.org) (`https://chainlist.org/rpcs.json`) at compare time. **No RPC URLs are hardcoded** in the package — only the chain name → chain id mapping (e.g. ethereum → 1).

```bash
pip install rpcbench
rpcbench --help
rpcbench compare --chain ethereum
```

Default **`--budget short`**. Add your own URLs with **`--endpoint URL`**, or merge a file with **`--endpoints FILE`**. Cap how many Chainlist RPCs to take with **`--chain-limit N`** (default 12).

## Caveats

- **Public RPCs rate-limit.** Expect `rate_limit` / HTTP 429 under load.
- **Short is the default**, not `long` / “Deep”.
- Needs network access to Chainlist when you pass `--chain`. Offline? Use `--endpoints` / `--endpoint` only.
- Filter keeps **keyless HTTPS** only (skips `wss://`, API-key placeholders, `${…}` templates). Not an SLA.

## Source

[DefiLlama/chainlist](https://github.com/defillama/chainlist) publishes `https://chainlist.org/rpcs.json`. RPCBench:

1. Resolves `--chain` to a chain id (`ethereum` → `1`).
2. GETs that JSON (timeout 20s).
3. Collects unique hosts with keyless `https://` RPCs, up to `--chain-limit`.
4. Names each endpoint from its hostname.

## Refresh process

Nothing to commit for URL churn: each `--chain` run pulls the current Chainlist dump. To extend support:

1. Add a chain name → `chain_id` / `family` entry in `src/rpcbench/catalog.py` (`_CHAINS`).
2. Document aliases in `--chain` help / this file.
3. Cover the new name in `tests/test_catalog.py` with a mocked Chainlist payload (no live fetch in unit tests).
4. Optionally smoke once against the public network before release.

Filter rules (`_is_keyless_https`) are the review surface — tighten there if Chainlist starts listing keyed-only hosts under a new pattern.

## Related flags

| Flag | Role |
| --- | --- |
| `--chain NAME` | Fetch Chainlist RPCs for that chain |
| `--chain-limit N` | Max keyless HTTPS RPCs from Chainlist (default 12; `0` = all) |
| `--endpoint URL` | Extra URL (repeatable) |
| `--endpoints FILE\|URL` | User file or single URL (optional with `--chain`) |
| `--budget short\|standard\|long` | Sample size; catalog default is `short` |
