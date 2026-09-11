"""rpcbench CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rpcbench import __version__
from rpcbench.config import ConfigError, load_targets
from rpcbench.consistency import BlockPinError, parse_block_pin
from rpcbench.freshness import DEFAULT_BLOCK_TIME_S, DEFAULT_STALE_BLOCKS
from rpcbench.diff import (
    DiffError,
    compare_reports,
    format_diff,
    format_diff_md,
    history_files,
    load_report,
    write_history,
)
from rpcbench.csv import format_csv
from rpcbench.html import format_html
from rpcbench.logs import DEFAULT_LOGS_RANGE, LOGS_RANGES, ranges_for
from rpcbench.markdown import format_md
from rpcbench.methods import MethodError, is_app_workload, request_units, resolve_workload
from rpcbench.report import RankError, format_json, format_run, normalize_rank_by, normalize_similar_band
from rpcbench.run import (
    DEFAULT_BATCH,
    MAX_BATCH,
    MAX_BURST,
    MODE_PAIRED,
    MODE_SEQUENTIAL,
    run_endpoints,
)
from rpcbench.safety import SafetyError, check_budget, kill_switch_reason
from rpcbench.tags import META_REQUESTS_PER_ENDPOINT


SAMPLE_BUDGETS: dict[str, dict[str, int | float]] = {
    # Sample count / duration. Not Nodeprobe Quick|Standard|Deep.
    "short": {
        "samples": 3,
        "warmup": 0,
        "timeout": 5.0,
        "max_duration": 30.0,
        "concurrency": 0,
    },
    "standard": {
        "samples": 10,
        "warmup": 1,
        "timeout": 10.0,
        "max_duration": 600.0,
        "concurrency": 0,
    },
    "long": {
        "samples": 50,
        "warmup": 2,
        "timeout": 15.0,
        "max_duration": 1800.0,
        "concurrency": 0,
    },
}
DEFAULT_MAX_REQUESTS_FLAG = 128


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
    _add_diff_parser(sub)
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
        help="Read-only method pack: head, chainId, or balance. Do not combine with --profile.",
    )
    run.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help=(
            "App mix alias: mix (= general), general, wallet, indexer, trading, nft. "
            "Same as --workload. Do not combine with --method."
        ),
    )
    run.add_argument(
        "--workload",
        nargs="?",
        const="general",
        default=None,
        choices=("general", "wallet", "indexer", "trading", "nft"),
        metavar="NAME",
        help=(
            "What you are building: general (default when the flag is present), "
            "wallet, indexer, trading, or nft. Weighted read-only mix; compose "
            "with --budget. Alias: --profile mix = general."
        ),
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
        "--budget",
        choices=tuple(SAMPLE_BUDGETS),
        default="standard",
        dest="sample_budget",
        help=(
            "Sample budget: short, standard (default), or long. "
            "Sets samples, warmup, timeout, and max duration. "
            "Not a Nodeprobe scan profile. HTTP cap is --max-requests."
        ),
    )
    run.add_argument(
        "--samples",
        type=int,
        default=None,
        help="Timed mix rounds after warmup (default: from --budget)",
    )
    run.add_argument(
        "--warmup",
        type=int,
        default=None,
        help="Warmup requests excluded from stats (default: from --budget)",
    )
    run.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Per-request timeout in seconds (default: from --budget)",
    )
    run.add_argument(
        "--max-requests",
        type=int,
        default=DEFAULT_MAX_REQUESTS_FLAG,
        dest="max_requests",
        help="Max HTTP requests for the whole run, including warmup (default: 128)",
    )
    run.add_argument(
        "--max-duration",
        type=float,
        default=None,
        metavar="SEC",
        help="Stop the run after this many seconds and still print a report (default: from --budget; 0 = no limit)",
    )
    run.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Max in-flight requests per paired wave (0 = all providers; default: from --budget). Not a load burst.",
    )
    run.add_argument(
        "--burst",
        type=int,
        default=0,
        metavar="N",
        help=(
            f"Overlap the first N timed samples (0=off, max {MAX_BURST}). "
            "Splits the existing sample budget; does not add requests."
        ),
    )
    run.add_argument(
        "--rps",
        type=float,
        default=0.0,
        metavar="N",
        help="Cap starts per second after --burst (0=off). Does not raise the request budget.",
    )
    run.add_argument(
        "--batch",
        type=int,
        nargs="?",
        const=DEFAULT_BATCH,
        default=0,
        metavar="N",
        help=(
            f"Compare a JSON-RPC batch of N calls vs the same N sent one-by-one "
            f"(0=off, omit N for {DEFAULT_BATCH}, max {MAX_BATCH}). "
            "Adds 1+N requests per endpoint; not mixed into ranking."
        ),
    )
    run.add_argument(
        "--logs-range",
        type=int,
        nargs="?",
        const=DEFAULT_LOGS_RANGE,
        default=0,
        metavar="N",
        help=(
            "Extra pinned eth_getLogs at 1, 10, 100, and up to N blocks "
            f"(0=off, omit N for {DEFAULT_LOGS_RANGE}, allowed {', '.join(str(n) for n in LOGS_RANGES)}). "
            "Mix logs stay one block. Not mixed into ranking."
        ),
    )
    run.add_argument(
        "--sequential",
        action="store_true",
        help="Run endpoints one after another instead of racing each sample (default is paired)",
    )
    run.add_argument(
        "--new-connection",
        action="store_true",
        help=(
            "Open a fresh TCP/TLS connection for every request "
            "(default: keep-alive). Handshake cost is visible; ranking still uses total RTT."
        ),
    )
    run.add_argument(
        "--http2",
        action="store_true",
        help=(
            "Prefer HTTP/2 via ALPN (falls back to HTTP/1.1 if the peer does not offer it). "
            "Not mixed into ranking."
        ),
    )
    run.add_argument(
        "--http1",
        action="store_true",
        help="Force HTTP/1.1 (default).",
    )
    run.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Seed for the shared request sequence (default: 0)",
    )
    run.add_argument(
        "--rank-by",
        default="p95",
        metavar="KEY",
        help="Ranking key: p95 (default), p50, p99, mean, or rps (throughput). Failed endpoints are listed last.",
    )
    run.add_argument(
        "--similar-band",
        type=float,
        default=0.10,
        metavar="FRAC",
        help="Relative similar-band on the rank key (default: 0.10 = 10%%). High error rate above this band is not a numbered place.",
    )
    run.add_argument(
        "--stale-blocks",
        type=int,
        default=DEFAULT_STALE_BLOCKS,
        metavar="N",
        help="Head lag (blocks vs cohort median) above this is stale (default: 2). Set per chain.",
    )
    run.add_argument(
        "--block-time",
        type=float,
        default=None,
        metavar="SEC",
        help=f"Seconds per block for lag time (default: {DEFAULT_BLOCK_TIME_S:g} or a known chain from eth_chainId).",
    )
    run.add_argument(
        "--block",
        default=None,
        metavar="HEX|N",
        help=(
            "Pin the head-hash check to this block (hex, decimal, or latest). "
            "Default: cohort median head from this run. Use when heads diverge by one block."
        ),
    )
    run.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help=(
            "Print the full report: Comparison, Reliability, Signals, Coverage, Methods, Timing, "
            "Transport, Tags, Burst, Providers, Capabilities, and per-sample rows"
        ),
    )
    run.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON report to stdout instead of the CLI table",
    )
    run.add_argument(
        "--html",
        action="store_true",
        help="Write a standalone HTML report to -o FILE (inline CSS/SVG, no CDN)",
    )
    run.add_argument(
        "--md",
        action="store_true",
        help="Print a GitHub-flavored markdown report (pasteable). -o FILE writes the same markdown",
    )
    run.add_argument(
        "--csv",
        action="store_true",
        help="Print a flat CSV (one row per provider). -o FILE writes the same CSV; .csv on -o also writes CSV",
    )
    run.add_argument(
        "--history",
        metavar="DIR",
        help="Append a JSON snapshot to DIR after the run (local history for rpcbench diff)",
    )
    run.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help=(
            "Write JSON to FILE, HTML when --html, markdown when --md, "
            "or CSV when --csv / FILE ends in .csv (CLI table still prints unless --json/--md/--csv)"
        ),
    )


def _add_diff_parser(sub) -> None:
    diff = sub.add_parser(
        "diff",
        help="Compare two JSON reports; exit 1 if the primary got worse beyond the similar-band",
    )
    diff.add_argument("old", nargs="?", metavar="OLD.json", help="Earlier rpcbench JSON report")
    diff.add_argument("new", nargs="?", metavar="NEW.json", help="Later rpcbench JSON report")
    diff.add_argument(
        "--history",
        metavar="DIR",
        help="Diff the two newest JSON files in DIR instead of passing paths",
    )
    diff.add_argument(
        "--similar-band",
        type=float,
        default=None,
        metavar="FRAC",
        help="Override the similar-band used for the primary regression check (default: from the new report)",
    )
    diff.add_argument(
        "--md",
        action="store_true",
        help="Print the diff as GitHub-flavored markdown",
    )


def _output_csv_path(args: argparse.Namespace) -> bool:
    if not args.output or args.html or args.md or args.json:
        return False
    return Path(args.output).suffix.lower() == ".csv"


def apply_sample_budget(args: argparse.Namespace) -> argparse.Namespace:
    """Fill samples/warmup/timeout/max-duration/concurrency from --budget unless set."""
    spec = SAMPLE_BUDGETS[args.sample_budget]
    if args.samples is None:
        args.samples = int(spec["samples"])
    if args.warmup is None:
        args.warmup = int(spec["warmup"])
    if args.timeout is None:
        args.timeout = float(spec["timeout"])
    if args.max_duration is None:
        args.max_duration = float(spec["max_duration"])
    if args.concurrency is None:
        args.concurrency = int(spec["concurrency"])
    return args


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command in {"run", "compare"}:
        return _cmd_run(args)
    if args.command == "diff":
        return _cmd_diff(args)
    parser.print_help()
    return 2


def _cmd_run(args: argparse.Namespace) -> int:
    stopped = kill_switch_reason()
    if stopped:
        print(f"rpcbench: disabled ({stopped})", file=sys.stderr)
        return 2
    if args.html and not args.output:
        print("rpcbench: --html needs -o FILE", file=sys.stderr)
        return 2
    if args.http1 and args.http2:
        print("rpcbench: pick --http1 or --http2, not both", file=sys.stderr)
        return 2
    formats = [name for name, on in (("json", args.json), ("md", args.md), ("csv", args.csv)) if on]
    if len(formats) > 1:
        print("rpcbench: pick --json, --md, or --csv", file=sys.stderr)
        return 2
    try:
        apply_sample_budget(args)
        config = load_targets(args.endpoints)
        method, workload = resolve_workload(
            profile=args.profile,
            workload=args.workload,
            method=args.method,
            preset=args.preset,
            params_json=args.params,
            allow_writes=args.allow_writes,
        )
        units = request_units(workload)
        needed = (
            len(config.endpoints)
            * units
            * (args.samples + args.warmup)
        )
        if not any(spec.method == "eth_blockNumber" for spec in workload):
            needed += len(config.endpoints)
        needed += len(config.endpoints)
        needed += len(config.endpoints) * META_REQUESTS_PER_ENDPOINT
        if args.batch > 0:
            needed += len(config.endpoints) * (1 + args.batch)
        if args.logs_range > 0:
            needed += len(config.endpoints) * len(ranges_for(args.logs_range))
        max_requests = args.max_requests
        extra_read = (
            is_app_workload(method) or args.batch > 0 or args.logs_range > 0
        )
        if extra_read and needed > max_requests:
            if args.max_requests == DEFAULT_MAX_REQUESTS_FLAG:
                max_requests = needed
            elif is_app_workload(method):
                raise SafetyError(
                    f"{method} needs {needed} requests "
                    f"({units} methods × {args.samples + args.warmup} × "
                    f"{len(config.endpoints)} endpoints); pass --max-requests {needed}"
                )
            elif args.logs_range > 0:
                n_ranges = len(ranges_for(args.logs_range))
                raise SafetyError(
                    f"--logs-range {args.logs_range} needs {needed} requests "
                    f"({len(config.endpoints)} endpoints × {n_ranges} ranges); "
                    f"pass --max-requests {needed}"
                )
            else:
                raise SafetyError(
                    f"--batch {args.batch} needs {needed} requests "
                    f"({len(config.endpoints)} endpoints × (1+{args.batch}) extra); "
                    f"pass --max-requests {needed}"
                )
        check_budget(max_requests)
    except (ConfigError, MethodError, SafetyError, RankError) as exc:
        print(f"rpcbench: {exc}", file=sys.stderr)
        return 2
    if (
        args.timeout <= 0
        or args.max_requests < 1
        or args.samples < 1
        or args.warmup < 0
        or args.max_duration < 0
        or args.concurrency < 0
        or args.stale_blocks < 0
        or args.burst < 0
        or args.burst > MAX_BURST
        or args.batch < 0
        or args.batch > MAX_BATCH
        or args.rps < 0
        or (args.block_time is not None and args.block_time <= 0)
    ):
        print(
            "rpcbench: --timeout must be > 0, --samples >= 1, "
            "--warmup >= 0, --max-requests >= 1, --max-duration >= 0, "
            f"--concurrency >= 0, --burst 0–{MAX_BURST}, --batch 0–{MAX_BATCH}, "
            "--rps >= 0, "
            "--stale-blocks >= 0, --block-time > 0",
            file=sys.stderr,
        )
        return 2
    if args.logs_range not in (0, *LOGS_RANGES):
        allowed = ", ".join(str(n) for n in LOGS_RANGES)
        print(
            f"rpcbench: --logs-range must be 0 (off) or {allowed}",
            file=sys.stderr,
        )
        return 2
    try:
        rank_by = normalize_rank_by(args.rank_by)
        similar_band = normalize_similar_band(args.similar_band)
        block_pin = parse_block_pin(args.block)
    except RankError as exc:
        print(f"rpcbench: {exc}", file=sys.stderr)
        return 2
    except BlockPinError as exc:
        print(f"rpcbench: {exc}", file=sys.stderr)
        return 2
    params = list(workload[0].params) if len(workload) == 1 else []
    result = run_endpoints(
        config,
        method=method,
        params=params,
        samples=args.samples,
        warmup=args.warmup,
        timeout=args.timeout,
        budget=max_requests,
        workload=workload,
        profile=method if is_app_workload(method) else "single",
        sample_budget=args.sample_budget,
        stale_blocks=args.stale_blocks,
        block_time_s=args.block_time,
        block_pin=block_pin,
        max_duration=args.max_duration,
        mode=MODE_SEQUENTIAL if args.sequential else MODE_PAIRED,
        seed=args.seed,
        concurrency=args.concurrency,
        burst=args.burst,
        rps=args.rps,
        new_connection=args.new_connection,
        http2=args.http2,
        batch=args.batch,
        logs_range=args.logs_range,
    )
    json_blob = None
    md_blob = None
    csv_blob = None
    write_csv = args.csv or _output_csv_path(args)
    need_json = bool(
        args.json
        or args.history
        or (args.output and not args.html and not args.md and not write_csv)
    )
    if need_json:
        json_blob = format_json(result, rank_by=rank_by, similar_band=similar_band)
    if args.md:
        md_blob = format_md(result, rank_by=rank_by, similar_band=similar_band)
    if write_csv:
        csv_blob = format_csv(result, rank_by=rank_by, similar_band=similar_band)
    if args.output:
        path = Path(args.output)
        try:
            if args.html:
                blob = format_html(result, rank_by=rank_by, similar_band=similar_band)
            elif args.md:
                blob = md_blob
            elif write_csv:
                blob = csv_blob
            else:
                blob = json_blob
            path.write_text(blob or "", encoding="utf-8")
        except OSError as exc:
            print(f"rpcbench: cannot write {path}: {exc}", file=sys.stderr)
            return 2
    if args.history:
        try:
            if json_blob is None:
                json_blob = format_json(
                    result, rank_by=rank_by, similar_band=similar_band
                )
            write_history(Path(args.history), json_blob)
        except OSError as exc:
            print(f"rpcbench: cannot write history: {exc}", file=sys.stderr)
            return 2
    if args.json:
        sys.stdout.write(json_blob or format_json(result, rank_by=rank_by, similar_band=similar_band))
    elif args.md:
        sys.stdout.write(md_blob or "")
    elif args.csv:
        sys.stdout.write(csv_blob or "")
    else:
        sys.stdout.write(format_run(result, verbose=args.verbose, rank_by=rank_by, similar_band=similar_band))
    if any(outcome.stats.n_ok for outcome in result.outcomes):
        return 0
    return 1


def _cmd_diff(args: argparse.Namespace) -> int:
    try:
        if args.similar_band is not None:
            band = normalize_similar_band(args.similar_band)
        else:
            band = None
        if args.history:
            if args.old or args.new:
                print("rpcbench: diff --history DIR or OLD.json NEW.json", file=sys.stderr)
                return 2
            files = history_files(Path(args.history))
            old_path, new_path = files[-2], files[-1]
        else:
            if not args.old or not args.new:
                print("rpcbench: diff needs OLD.json NEW.json (or --history DIR)", file=sys.stderr)
                return 2
            old_path, new_path = Path(args.old), Path(args.new)
        old = load_report(old_path)
        new = load_report(new_path)
        diff = compare_reports(old, new, similar_band=band)
    except (DiffError, RankError, OSError) as exc:
        print(f"rpcbench: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(format_diff_md(diff) if args.md else format_diff(diff))
    return 1 if diff.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
