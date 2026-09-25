"""rpcbench CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rpcbench import __version__
from rpcbench.config import ConfigError, load_targets
from rpcbench.consistency import BlockPinError, parse_block_pin
from rpcbench.freshness import DEFAULT_BLOCK_TIME_S, DEFAULT_STALE_BLOCKS
from rpcbench.history import DEFAULT_LOOKBACK
from rpcbench.capture import (
    CaptureError,
    dump_jsonl,
    format_replay,
    format_replay_csv,
    format_replay_html,
    format_replay_json,
    format_replay_md,
    load_jsonl,
    record_calls,
    reject_write_calls,
    replay_calls,
)
from rpcbench.family import (
    FAMILY_EVM,
    benchmark_family,
    meta_requests_for,
    resolve_benchmark_family,
)
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
from rpcbench.methods import (
    MethodError,
    apply_simulate,
    canonical_workload,
    is_app_workload,
    request_units,
    resolve_workload,
)
from rpcbench.profile import as_profile_path, has_dynamic_source, hint_request_count
from rpcbench.report import RankError, format_json, format_run, normalize_rank_by, normalize_similar_band
from rpcbench.run import (
    DEFAULT_BATCH,
    DEFAULT_INFLIGHT,
    DEFAULT_THROUGHPUT,
    DEFAULT_WEBSOCKET,
    MAX_BATCH,
    MAX_BURST,
    MAX_INFLIGHT,
    MAX_THROUGHPUT,
    MAX_WEBSOCKET,
    MODE_PAIRED,
    MODE_SEQUENTIAL,
    run_endpoints,
)
from rpcbench.safety import SafetyError, check_budget, kill_switch_reason

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


_LAB_DESTS = frozenset(
    {
        "method",
        "preset",
        "profile",
        "params",
        "allow_writes",
        "samples",
        "warmup",
        "timeout",
        "max_requests",
        "max_duration",
        "concurrency",
        "burst",
        "rps",
        "throughput",
        "websocket",
        "batch",
        "logs_range",
        "simulate",
        "archive",
        "lookback",
        "sequential",
        "new_connection",
        "http2",
        "http1",
        "seed",
        "rank_by",
        "similar_band",
        "stale_blocks",
        "block_time",
        "block",
        "verbose",
        "family",
    }
)
_SIMULATE_JOBS = frozenset({"wallet", "trading"})
_JOB_EPILOG = (
    "No other flags: general workload, short budget. "
    "wallet and trading add simulate. "
    "indexer adds logs-range, archive, and lookback. "
    "--budget is size only (not archive, WebSocket, or tracing). "
    "Lab flags: --help-all"
)


def build_parser(*, full: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpcbench",
        description="Which RPC endpoint is ready for this workload, from this machine.",
        epilog=_JOB_EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"rpcbench {__version__}")
    parser.add_argument(
        "--help-all",
        action="store_true",
        help="Show lab flags, run, record, and replay",
    )
    sub = parser.add_subparsers(dest="command")
    _add_run_parser(
        sub,
        "run",
        "Same as compare",
        full=full,
        show=full,
    )
    _add_run_parser(
        sub,
        "compare",
        "Rank endpoints for a workload and print a verdict",
        full=full,
        show=True,
    )
    _add_diff_parser(sub)
    _add_record_parser(sub, show=full)
    _add_replay_parser(sub, show=full)
    return parser


def _add_run_parser(sub, name: str, help_text: str, *, full: bool, show: bool) -> None:
    run = sub.add_parser(
        name,
        help=help_text if show else argparse.SUPPRESS,
        epilog=_JOB_EPILOG,
    )
    run.add_argument(
        "--help-all",
        action="store_true",
        help="Show lab flags",
    )
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
            "App mix: mix (= general), general, wallet, indexer, trading, nft, "
            "tracing, or a YAML file. Same as --workload for named mixes. "
            "Do not combine with --method."
        ),
    )
    run.add_argument(
        "--workload",
        nargs="?",
        const="general",
        default=None,
        choices=("general", "wallet", "indexer", "trading", "nft", "tracing"),
        metavar="NAME",
        help=(
            "Job: general (default), wallet, indexer, trading, nft, or tracing. "
            "wallet and trading add simulate. indexer adds logs-range, archive, "
            "and lookback. tracing times optional trace_block and debug_traceCall. "
            "Compose with --budget. --profile mix is an alias for general."
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
        default=None,
        dest="sample_budget",
        help=(
            "Sample size: short (default when no other flags), standard, or long. "
            "Sets samples, warmup, timeout, and max duration. "
            "Does not enable archive, WebSocket, or tracing. "
            "Not a Nodeprobe scan profile. HTTP cap is --max-requests."
        ),
    )
    run.add_argument(
        "--family",
        default=None,
        metavar="NAME",
        help=(
            "Benchmark family for every endpoint (overrides the file). "
            "evm (default), solana, substrate, cosmos, aptos, sui, or near. "
            "auto detects from eth_chainId, getHealth, system_health, status, "
            "sui checkpoint, network_info, or ledger GET. "
            "Other families error. Not a scan."
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
        nargs="?",
        const=DEFAULT_INFLIGHT,
        default=0,
        metavar="N",
        help=(
            f"Overlap N extra copies of the primary method, then the same N serial "
            f"(0=off, omit N for {DEFAULT_INFLIGHT}, max {MAX_INFLIGHT}). "
            "Adds 2N requests per endpoint; not mixed into ranking. "
            "Not an unbounded load test."
        ),
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
        help="Cap starts per second after --burst and during --throughput (0=off). Does not raise the request budget.",
    )
    run.add_argument(
        "--throughput",
        type=int,
        nargs="?",
        const=DEFAULT_THROUGHPUT,
        default=0,
        metavar="N",
        help=(
            f"Extra serial copies of the primary method for successful req/s "
            f"(0=off, omit N for {DEFAULT_THROUGHPUT}, max {MAX_THROUGHPUT}). "
            "Adds N requests per endpoint; paced by --rps; 429 is a rejected request. "
            "Not mixed into ranking. Not an unbounded load test."
        ),
    )
    run.add_argument(
        "--websocket",
        type=float,
        nargs="?",
        const=DEFAULT_WEBSOCKET,
        default=0.0,
        metavar="SEC",
        help=(
            "Timed WebSocket connect + eth_subscribe newHeads "
            f"(0=off, omit SEC for {DEFAULT_WEBSOCKET:g}s, max {MAX_WEBSOCKET:g}s). "
            "Needs a ws/wss URL on the endpoint. Missing WS is not configured. "
            "Not mixed into ranking. Does not consume --max-requests."
        ),
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
        default=None,
        metavar="N",
        help=(
            "Extra pinned eth_getLogs at 1, 10, 100, and up to N blocks "
            f"(0=off, omit N for {DEFAULT_LOGS_RANGE}, allowed {', '.join(str(n) for n in LOGS_RANGES)}). "
            "Mix logs stay one block. Not mixed into ranking."
        ),
    )
    sim = run.add_mutually_exclusive_group()
    sim.add_argument(
        "--simulate",
        action="store_const",
        const=True,
        dest="simulate",
        default=None,
        help=(
            "Add read-only eth_call, eth_estimateGas, and eth_simulateV1 "
            "(fixture tx, no send). Missing simulateV1 is skip, not a crash. "
            "On for wallet and trading unless --no-simulate."
        ),
    )
    sim.add_argument(
        "--no-simulate",
        action="store_const",
        const=False,
        dest="simulate",
        help="Do not add simulate steps (overrides wallet and trading).",
    )
    archive = run.add_mutually_exclusive_group()
    archive.add_argument(
        "--archive",
        action="store_const",
        const=True,
        dest="archive",
        default=None,
        help=(
            "Probe historical state (eth_getBalance at genesis). "
            "Reports yes / no / unknown / rate-limited. Not mixed into ranking. "
            "On for indexer unless --no-archive."
        ),
    )
    archive.add_argument(
        "--no-archive",
        action="store_const",
        const=False,
        dest="archive",
        help="Do not probe archive (overrides indexer and --lookback).",
    )
    run.add_argument(
        "--lookback",
        type=int,
        nargs="?",
        const=DEFAULT_LOOKBACK,
        default=None,
        metavar="N",
        help=(
            "Timed eth_getBalance at pin−N vs latest "
            f"(0=off, omit N for {DEFAULT_LOOKBACK}). "
            "Skips when archive/history is missing. Not mixed into ranking."
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
        help="Seed for the shared request sequence and YAML payload sources (default: 0)",
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
    if not full:
        for action in run._actions:
            if action.dest in _LAB_DESTS:
                action.help = argparse.SUPPRESS


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


def _add_record_parser(sub, *, show: bool = True) -> None:
    rec = sub.add_parser(
        "record",
        help=(
            "Write a JSONL capture of a mix (method + params per line)"
            if show
            else argparse.SUPPRESS
        ),
    )
    rec.add_argument(
        "--endpoints",
        metavar="FILE|URL",
        help="YAML/JSON file, or a single http(s) URL (needed to bind YAML sources)",
    )
    rec.add_argument("--method", default=None, help="JSON-RPC method to record")
    rec.add_argument(
        "--preset",
        default=None,
        metavar="NAME",
        help="Read-only method pack: head, chainId, or balance",
    )
    rec.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help="App mix or a YAML file. Same as --workload for named mixes.",
    )
    rec.add_argument(
        "--workload",
        nargs="?",
        const="general",
        default=None,
        choices=("general", "wallet", "indexer", "trading", "nft", "tracing"),
        metavar="NAME",
        help="App mix: general (omit name), wallet, indexer, trading, nft, tracing",
    )
    rec.add_argument(
        "--params",
        default=None,
        metavar="JSON",
        help='JSON array of params, e.g. \'["0x0","latest"]\'',
    )
    rec.add_argument(
        "--allow-writes",
        action="store_true",
        help="Allow write methods (eth_send*, personal_*, …). Default is read-only.",
    )
    rec.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Mix rounds to write (default: 1). No warmup.",
    )
    rec.add_argument("--seed", type=int, default=0, help="Shared sequence stamp")
    rec.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout when binding YAML sources (default: 10)",
    )
    rec.add_argument(
        "--max-requests",
        type=int,
        default=DEFAULT_MAX_REQUESTS_FLAG,
        help="HTTP cap when binding YAML sources (default: 128)",
    )
    rec.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Write JSONL to FILE (default: stdout)",
    )


def _add_replay_parser(sub, *, show: bool = True) -> None:
    rep = sub.add_parser(
        "replay",
        help=(
            "Replay a JSONL capture in lockstep and diff bodies across providers"
            if show
            else argparse.SUPPRESS
        ),
    )
    rep.add_argument(
        "--endpoints",
        required=True,
        metavar="FILE|URL",
        help="YAML/JSON file, or a single http(s) URL (localhost is allowed)",
    )
    rep.add_argument(
        "--from",
        dest="capture",
        required=True,
        metavar="FILE",
        help="JSONL capture (method/params per line). Use - for stdin",
    )
    rep.add_argument(
        "--allow-writes",
        action="store_true",
        help="Allow write methods (eth_send*, personal_*, …). Default is read-only.",
    )
    rep.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds (default: 10)",
    )
    rep.add_argument(
        "--max-requests",
        type=int,
        default=DEFAULT_MAX_REQUESTS_FLAG,
        help="HTTP cap for the whole replay (default: 128)",
    )
    rep.add_argument(
        "--max-duration",
        type=float,
        default=0.0,
        help="Stop after SEC seconds and still print a report (default: 0 = no limit)",
    )
    rep.add_argument(
        "--new-connection",
        action="store_true",
        help="Fresh TCP/TLS every request. Default is keep-alive",
    )
    rep.add_argument("--http2", action="store_true", help="Prefer HTTP/2 via ALPN")
    rep.add_argument("--http1", action="store_true", help="Force HTTP/1.1 (default)")
    rep.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print canonical JSON body diffs for mismatched calls",
    )
    rep.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON report to stdout instead of the CLI table",
    )
    rep.add_argument(
        "--html",
        action="store_true",
        help="Write a standalone HTML report to -o FILE",
    )
    rep.add_argument(
        "--md",
        action="store_true",
        help="Print a GitHub-flavored markdown report",
    )
    rep.add_argument(
        "--csv",
        action="store_true",
        help="Print a flat CSV (one row per provider)",
    )
    rep.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Write JSON/HTML/markdown/CSV to FILE",
    )


def _output_csv_path(args: argparse.Namespace) -> bool:
    if not args.output or args.html or args.md or args.json:
        return False
    return Path(args.output).suffix.lower() == ".csv"


def apply_job(args: argparse.Namespace) -> argparse.Namespace:
    """Bare compare is the general job. Named workloads turn on their extras.

    Unset extra-read flags stay None until here, so an explicit 0 / --no-* wins.
    --budget long does not turn extras on.
    """
    bare = not any((args.method, args.preset, args.profile, args.workload))
    if bare:
        args.workload = "general"
    if args.sample_budget is None:
        args.sample_budget = "short" if bare else "standard"
    job = _named_job(args)
    if args.logs_range is None:
        args.logs_range = DEFAULT_LOGS_RANGE if job == "indexer" else 0
    if args.lookback is None:
        args.lookback = DEFAULT_LOOKBACK if job == "indexer" else 0
    if args.simulate is None:
        args.simulate = job in _SIMULATE_JOBS
    if args.archive is None:
        args.archive = job == "indexer" or args.lookback > 0
    return args


def _named_job(args: argparse.Namespace) -> str | None:
    """Catalog mix this run asked for, or None for a single method or YAML file."""
    if args.method or args.preset:
        return None
    raw = args.workload or args.profile
    if not raw or as_profile_path(raw) is not None:
        return None
    return canonical_workload(raw)


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
    raw = list(sys.argv[1:] if argv is None else argv)
    full = "--help-all" in raw
    if full:
        raw = [arg for arg in raw if arg != "--help-all"]
        if "--help" not in raw and "-h" not in raw:
            raw.append("--help")
    parser = build_parser(full=full)
    args = parser.parse_args(raw)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command in {"run", "compare"}:
        return _cmd_run(args)
    if args.command == "diff":
        return _cmd_diff(args)
    if args.command == "record":
        return _cmd_record(args)
    if args.command == "replay":
        return _cmd_replay(args)
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
        apply_job(args)
        config = load_targets(args.endpoints)
        detect_timeout = (
            float(args.timeout)
            if args.timeout is not None
            else float(SAMPLE_BUDGETS[args.sample_budget]["timeout"])
        )
        family_name = resolve_benchmark_family(
            config, override=args.family, timeout=detect_timeout
        )
        if family_name != FAMILY_EVM:
            # EVM-only extras: skip with a reason in the run path; do not budget them.
            args.simulate = False
            args.logs_range = 0
            args.archive = False
            args.lookback = 0
        plan = resolve_workload(
            profile=args.profile,
            workload=args.workload,
            method=args.method,
            preset=args.preset,
            params_json=args.params,
            allow_writes=args.allow_writes,
            family=family_name,
        )
        if plan.timeout is not None and args.timeout is None:
            args.timeout = plan.timeout
        apply_sample_budget(args)
        head_method = benchmark_family(family_name).head_method
        method, workload = plan.label, plan.steps
        if args.simulate:
            workload = apply_simulate(workload)
            if not is_app_workload(method):
                method = "simulate"
        units = request_units(workload)
        needed = (
            len(config.endpoints)
            * units
            * (args.samples + args.warmup)
        )
        if not any(spec.method == head_method for spec in workload):
            needed += len(config.endpoints)
        needed += len(config.endpoints)
        needed += len(config.endpoints) * meta_requests_for(family_name)
        needed += len(config.endpoints) * hint_request_count(workload)
        if args.batch > 0:
            needed += len(config.endpoints) * (1 + args.batch)
        if args.concurrency > 0:
            needed += len(config.endpoints) * (2 * args.concurrency)
        if args.throughput > 0:
            needed += len(config.endpoints) * args.throughput
        if args.logs_range > 0:
            needed += len(config.endpoints) * len(ranges_for(args.logs_range))
        if args.archive:
            needed += len(config.endpoints)
        if args.lookback > 0:
            needed += len(config.endpoints) * 2
        max_requests = args.max_requests
        extra_read = (
            is_app_workload(method)
            or args.batch > 0
            or args.concurrency > 0
            or args.throughput > 0
            or args.logs_range > 0
            or args.simulate
            or args.archive
            or args.lookback > 0
            or has_dynamic_source(workload)
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
            elif args.lookback > 0:
                raise SafetyError(
                    f"--lookback {args.lookback} needs {needed} requests "
                    f"({len(config.endpoints)} endpoints × 2 extra); "
                    f"pass --max-requests {needed}"
                )
            elif args.archive:
                raise SafetyError(
                    f"--archive needs {needed} requests "
                    f"({len(config.endpoints)} endpoints × 1 extra); "
                    f"pass --max-requests {needed}"
                )
            elif args.concurrency > 0:
                raise SafetyError(
                    f"--concurrency {args.concurrency} needs {needed} requests "
                    f"({len(config.endpoints)} endpoints × {2 * args.concurrency} extra); "
                    f"pass --max-requests {needed}"
                )
            elif args.throughput > 0:
                raise SafetyError(
                    f"--throughput {args.throughput} needs {needed} requests "
                    f"({len(config.endpoints)} endpoints × {args.throughput} extra); "
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
        or args.concurrency > MAX_INFLIGHT
        or args.stale_blocks < 0
        or args.burst < 0
        or args.burst > MAX_BURST
        or args.batch < 0
        or args.batch > MAX_BATCH
        or args.throughput < 0
        or args.throughput > MAX_THROUGHPUT
        or args.rps < 0
        or args.lookback < 0
        or args.websocket < 0
        or args.websocket > MAX_WEBSOCKET
        or (args.block_time is not None and args.block_time <= 0)
    ):
        print(
            "rpcbench: --timeout must be > 0, --samples >= 1, "
            "--warmup >= 0, --max-requests >= 1, --max-duration >= 0, "
            f"--concurrency 0–{MAX_INFLIGHT}, --burst 0–{MAX_BURST}, --batch 0–{MAX_BATCH}, "
            f"--throughput 0–{MAX_THROUGHPUT}, "
            f"--websocket 0–{MAX_WEBSOCKET:g}, "
            "--rps >= 0, --lookback >= 0, "
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
        concurrency=0,
        inflight=args.concurrency,
        burst=args.burst,
        rps=args.rps,
        throughput=args.throughput,
        new_connection=args.new_connection,
        http2=args.http2,
        batch=args.batch,
        logs_range=args.logs_range,
        profile_notes=plan.notes,
        simulate=args.simulate,
        archive=args.archive,
        lookback=args.lookback,
        websocket=args.websocket,
        family=family_name,
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


def _cmd_record(args: argparse.Namespace) -> int:
    stopped = kill_switch_reason()
    if stopped:
        print(f"rpcbench: disabled ({stopped})", file=sys.stderr)
        return 2
    if args.samples < 1:
        print("rpcbench: --samples must be >= 1", file=sys.stderr)
        return 2
    try:
        check_budget(args.max_requests)
        plan = resolve_workload(
            profile=args.profile,
            workload=args.workload,
            method=args.method,
            preset=args.preset,
            params_json=args.params,
            allow_writes=args.allow_writes,
        )
        config = load_targets(args.endpoints) if args.endpoints else None
        calls = record_calls(
            plan.steps,
            samples=args.samples,
            config=config,
            seed=args.seed,
            timeout=args.timeout,
            budget=args.max_requests,
        )
        reject_write_calls(calls, allow_writes=args.allow_writes)
    except (ConfigError, MethodError, SafetyError, CaptureError) as exc:
        print(f"rpcbench: {exc}", file=sys.stderr)
        return 2
    blob = dump_jsonl(calls)
    if args.output:
        path = Path(args.output)
        try:
            path.write_text(blob, encoding="utf-8")
        except OSError as exc:
            print(f"rpcbench: cannot write {path}: {exc}", file=sys.stderr)
            return 2
        return 0
    sys.stdout.write(blob)
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
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
    if args.timeout <= 0 or args.max_requests < 1 or args.max_duration < 0:
        print(
            "rpcbench: --timeout must be > 0, --max-requests >= 1, --max-duration >= 0",
            file=sys.stderr,
        )
        return 2
    try:
        check_budget(args.max_requests)
        config = load_targets(args.endpoints)
        if args.capture == "-":
            calls = load_jsonl(sys.stdin)
            source = "stdin"
        else:
            calls = load_jsonl(args.capture)
            source = str(args.capture)
        reject_write_calls(calls, allow_writes=args.allow_writes)
        needed = len(config.endpoints) * len(calls)
        max_requests = args.max_requests
        if needed > max_requests:
            if args.max_requests == DEFAULT_MAX_REQUESTS_FLAG:
                max_requests = needed
            else:
                raise SafetyError(
                    f"replay needs {needed} requests "
                    f"({len(config.endpoints)} endpoints × {len(calls)} calls); "
                    f"pass --max-requests {needed}"
                )
        from rpcbench.rpc import make_client

        client = make_client(
            timeout=args.timeout,
            new_connection=args.new_connection,
            http2=args.http2,
        )
        try:
            result = replay_calls(
                config,
                calls,
                source=source,
                timeout=args.timeout,
                budget=max_requests,
                max_duration=args.max_duration,
                allow_writes=args.allow_writes,
                client=client,
            )
        finally:
            client.close()
    except (ConfigError, MethodError, SafetyError, CaptureError) as exc:
        print(f"rpcbench: {exc}", file=sys.stderr)
        return 2
    json_blob = None
    md_blob = None
    csv_blob = None
    write_csv = args.csv or (
        bool(args.output)
        and not args.html
        and not args.md
        and not args.json
        and Path(args.output).suffix.lower() == ".csv"
    )
    need_json = bool(
        args.json or (args.output and not args.html and not args.md and not write_csv)
    )
    if need_json:
        json_blob = format_replay_json(result)
    if args.md:
        md_blob = format_replay_md(result)
    if write_csv:
        csv_blob = format_replay_csv(result)
    if args.output:
        path = Path(args.output)
        try:
            if args.html:
                blob = format_replay_html(result)
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
    if args.json:
        sys.stdout.write(json_blob or format_replay_json(result))
    elif args.md:
        sys.stdout.write(md_blob or "")
    elif args.csv:
        sys.stdout.write(csv_blob or "")
    else:
        sys.stdout.write(format_replay(result, verbose=args.verbose))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
