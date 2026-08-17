"""rpcbench CLI."""

from __future__ import annotations

import argparse
import sys

from rpcbench import __version__
from rpcbench.config import ConfigError, load_endpoints
from rpcbench.methods import MethodError, resolve_method
from rpcbench.run import format_run, run_endpoints


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpcbench",
        description="Measure RPC quality and compare providers.",
    )
    parser.add_argument("--version", action="version", version=f"rpcbench {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser(
        "run",
        help="Measure JSON-RPC round-trip latency for configured endpoints",
    )
    run.add_argument(
        "--endpoints",
        required=True,
        metavar="FILE",
        help="YAML or JSON file with named endpoints",
    )
    run.add_argument(
        "--method",
        default=None,
        help="JSON-RPC method (default: eth_blockNumber). Do not combine with --preset.",
    )
    run.add_argument(
        "--preset",
        default=None,
        metavar="NAME",
        help="Read-only method pack: head, chainId, or balance",
    )
    run.add_argument(
        "--params",
        default=None,
        metavar="JSON",
        help='JSON array of params, e.g. \'["0x0","latest"]\'',
    )
    run.add_argument(
        "--samples",
        type=int,
        default=10,
        help="Timed samples per endpoint after warmup (default: 10)",
    )
    run.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Warmup requests excluded from min/mean/max (default: 1)",
    )
    run.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds (default: 10)",
    )
    run.add_argument(
        "--budget",
        type=int,
        default=128,
        help="Max HTTP requests for the whole run, including warmup (default: 128)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "run":
        return _cmd_run(args)
    parser.print_help()
    return 2


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        config = load_endpoints(args.endpoints)
        method, params = resolve_method(
            method=args.method, preset=args.preset, params_json=args.params
        )
    except (ConfigError, MethodError) as exc:
        print(f"rpcbench: {exc}", file=sys.stderr)
        return 2
    if args.timeout <= 0 or args.budget < 1 or args.samples < 1 or args.warmup < 0:
        print(
            "rpcbench: --timeout must be > 0, --samples >= 1, "
            "--warmup >= 0, --budget >= 1",
            file=sys.stderr,
        )
        return 2
    result = run_endpoints(
        config,
        method=method,
        params=params,
        samples=args.samples,
        warmup=args.warmup,
        timeout=args.timeout,
        budget=args.budget,
    )
    sys.stdout.write(format_run(result))
    if any(outcome.stats.n_ok for outcome in result.outcomes):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
