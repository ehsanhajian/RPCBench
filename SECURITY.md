# Security Policy

## Reporting

Use [GitHub private vulnerability reporting](https://github.com/ehsanhajian/RPCBench/security/advisories/new) for anything that could leak credentials, enable abuse of third-party RPCs, or otherwise should not be public first.

Non-sensitive bugs can be a [public issue](https://github.com/ehsanhajian/RPCBench/issues/new).

RPCBench is a local benchmark CLI. It does not custody keys or user accounts. It is **not** a security scanner ([Nodeprobe](https://github.com/ehsanhajian/nodeprobe)).

## Scope notes

In scope: secret handling in reports/config, unsafe defaults (unbounded load, write methods), dependency issues.

Out of scope: RPC provider infrastructure, and security-surface checks that belong in Nodeprobe (see `docs/BOUNDARY.md`).
