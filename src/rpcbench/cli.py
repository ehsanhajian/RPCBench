"""rpcbench CLI."""

from __future__ import annotations

import argparse
import sys

from rpcbench import __version__
from rpcbench.config import ConfigError, load_targets
from rpcbench.methods import MethodError, resolve_method
from rpcbench.report import format_run
from rpcbench.run import run_endpoints
from rpcbench.safety import SafetyError, check_budget, kill_switch_reason


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpcbench",
        description="Measure RPC quality and compare providers.",
    )
    parser.add_argument("--version", action="version", version=f"rpcbench {__version__}")
    sub = parser.add_subparsers(dest="command")
    _add_run_parser(
        sub,
        "run",
        "Measure JSON-RPC round-trip latency and print a comparison report",
    )
    _add_run_parser(
        sub,
        "compare",
        "Same as run: print a ranked CLI report for configured endpoints",
    )
    return parser


def _add_run_parser(sub, name: str, help_text: str) -> None:
    run = sub.add_parser(name, help=help_text)
    run.add_argument(
        "--endpoints",
        required=True,
        metavar="FILE|URL",
        help="YAML/JSON file, or a single http(s) URL (localhost is allowed)",
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
        "--allow-writes",
        action="store_true",
        help="Allow write methods (eth_send*, personal_*, …). Default is read-only.",
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
        help="Warmup requests excluded from stats (default: 1)",
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
    run.add_argument(
        "--max-duration",
        type=float,
        default=600.0,
        metavar="SEC",
        help="Stop the run after this many seconds and still print a report (default: 600; 0 = no limit)",
    )
    run.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Max in-flight requests (only 1 is supported)",
    )
    run.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print per-sample latency and error-class detail",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command in {"run", "compare"}:
        return _cmd_run(args)
    parser.print_help()
    return 2


def _cmd_run(args: argparse.Namespace) -> int:
    stopped = kill_switch_reason()
    if stopped:
        print(f"rpcbench: disabled ({stopped})", file=sys.stderr)
        return 2
    try:
        check_budget(args.budget)
        config = load_targets(args.endpoints)
        method, params = resolve_method(
            method=args.method,
            preset=args.preset,
            params_json=args.params,
            allow_writes=args.allow_writes,
        )
    except (ConfigError, MethodError, SafetyError) as exc:
        print(f"rpcbench: {exc}", file=sys.stderr)
        return 2
    if (
        args.timeout <= 0
        or args.budget < 1
        or args.samples < 1
        or args.warmup < 0
        or args.max_duration < 0
    ):
        print(
            "rpcbench: --timeout must be > 0, --samples >= 1, "
            "--warmup >= 0, --budget >= 1, --max-duration >= 0",
            file=sys.stderr,
        )
        return 2
    if args.concurrency != 1:
        print("rpcbench: only --concurrency 1 is supported", file=sys.stderr)
        return 2
    result = run_endpoints(
        config,
        method=method,
        params=params,
        samples=args.samples,
        warmup=args.warmup,
        timeout=args.timeout,
        budget=args.budget,
        max_duration=args.max_duration,
    )
    sys.stdout.write(format_run(result, verbose=args.verbose))
    if any(outcome.stats.n_ok for outcome in result.outcomes):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
