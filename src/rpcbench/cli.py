"""rpcbench CLI."""

from __future__ import annotations

import argparse
import sys

from rpcbench import __version__
from rpcbench.config import ConfigError, load_endpoints
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
        help="Probe configured endpoints with a cheap JSON-RPC method",
    )
    run.add_argument(
        "--endpoints",
        required=True,
        metavar="FILE",
        help="YAML or JSON file with named endpoints",
    )
    run.add_argument(
        "--method",
        default="eth_blockNumber",
        help="JSON-RPC method to call (default: eth_blockNumber)",
    )
    run.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds (default: 10)",
    )
    run.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retries on timeout/connection errors (default: 2)",
    )
    run.add_argument(
        "--budget",
        type=int,
        default=32,
        help="Max HTTP requests for the whole run (default: 32)",
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
    except ConfigError as exc:
        print(f"rpcbench: {exc}", file=sys.stderr)
        return 2
    if args.timeout <= 0 or args.retries < 0 or args.budget < 1:
        print(
            "rpcbench: --timeout must be > 0, --retries >= 0, --budget >= 1",
            file=sys.stderr,
        )
        return 2
    result = run_endpoints(
        config,
        method=args.method,
        timeout=args.timeout,
        retries=args.retries,
        budget=args.budget,
    )
    sys.stdout.write(format_run(result))
    if any(outcome.probe.ok for outcome in result.outcomes):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
