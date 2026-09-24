"""JSONL traffic capture and lockstep replay. Not mixed into ranking."""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from rpcbench.config import BenchConfig
from rpcbench.methods import CallSpec, MethodError, is_write_method
from rpcbench.profile import has_dynamic_source
from rpcbench.rpc import ProbeResult, RequestBudget, make_client
from rpcbench.run import (
    _bind_from_chain,
    _probe_wave,
    expand_steps,
    percentile,
)
from rpcbench.watermark import (
    DOCS_BOUNDARY,
    DOCS_METHODOLOGY,
    FAMILY_EVM,
    family_token,
    git_sha as current_git_sha,
    utc_stamp,
    vantage_label,
)
from rpcbench import __version__

MAX_CAPTURE_CALLS = 10_000
_DIFF_CHARS = 2000


class CaptureError(ValueError):
    pass


@dataclass(frozen=True)
class CapturedCall:
    method: str
    params: tuple[Any, ...]


@dataclass(frozen=True)
class ReplayHit:
    name: str
    ok: bool
    latency_ms: float | None
    body_hash: str | None
    error: str | None
    error_class: str | None
    result: Any = None


@dataclass(frozen=True)
class ReplayStep:
    index: int
    method: str
    params: tuple[Any, ...]
    hits: tuple[ReplayHit, ...]
    match: bool
    body_mismatch: bool
    status_mismatch: bool
    error_mismatch: bool
    canonical_hash: str | None

    @property
    def note(self) -> str:
        bits = []
        if self.body_mismatch:
            bits.append("bodies")
        if self.status_mismatch:
            bits.append("status")
        if self.error_mismatch:
            bits.append("error")
        if not bits:
            return "—"
        return ",".join(bits)


@dataclass(frozen=True)
class ReplayProvider:
    name: str
    n_ok: int
    n_fail: int
    match: int
    mismatch: int
    p95_ms: float | None
    error_rate: float | None


@dataclass(frozen=True)
class ReplayResult:
    source: str
    calls: tuple[CapturedCall, ...]
    steps: tuple[ReplayStep, ...]
    providers: tuple[ReplayProvider, ...]
    timeout: float
    budget: int
    budget_remaining: int
    allow_writes: bool
    git_sha: str | None = None
    started_at: str | None = None
    vantage: str | None = None
    family: str = FAMILY_EVM

    @property
    def n_match(self) -> int:
        return sum(1 for step in self.steps if step.match)

    @property
    def n_mismatch(self) -> int:
        return sum(1 for step in self.steps if not step.match)

    @property
    def n_body_mismatch(self) -> int:
        return sum(1 for step in self.steps if step.body_mismatch)

    @property
    def n_status_mismatch(self) -> int:
        return sum(1 for step in self.steps if step.status_mismatch)

    @property
    def n_error_mismatch(self) -> int:
        return sum(1 for step in self.steps if step.error_mismatch)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def body_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:12]


def pretty_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, indent=2)


def load_jsonl(source: str | Path | TextIO) -> tuple[CapturedCall, ...]:
    """Read method/params objects. Full JSON-RPC requests are accepted."""
    if _is_stream(source):
        text = source.read()
        label = getattr(source, "name", "stdin") or "stdin"
    else:
        path = Path(source)
        if not path.is_file():
            raise CaptureError(f"capture file not found: {path}")
        text = path.read_text(encoding="utf-8")
        label = str(path)
    calls: list[CapturedCall] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CaptureError(f"{label}:{i}: invalid JSON ({exc})") from exc
        if not isinstance(item, dict):
            raise CaptureError(f"{label}:{i}: expected a JSON object")
        method = item.get("method")
        if not isinstance(method, str) or not method.strip():
            raise CaptureError(f"{label}:{i}: method is required")
        params = item.get("params", [])
        if params is None:
            params = []
        if not isinstance(params, list):
            raise CaptureError(f"{label}:{i}: params must be a JSON array")
        calls.append(CapturedCall(method=method.strip(), params=tuple(params)))
        if len(calls) > MAX_CAPTURE_CALLS:
            raise CaptureError(
                f"{label}: more than {MAX_CAPTURE_CALLS} calls "
                "(raise the capture cap is out of scope)"
            )
    if not calls:
        raise CaptureError(f"{label}: capture is empty")
    return tuple(calls)


def dump_jsonl(calls: tuple[CapturedCall, ...] | list[CapturedCall]) -> str:
    lines = [
        json.dumps(
            {"method": call.method, "params": list(call.params)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for call in calls
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def reject_write_calls(
    calls: tuple[CapturedCall, ...] | list[CapturedCall],
    *,
    allow_writes: bool,
) -> None:
    if allow_writes:
        return
    for call in calls:
        if is_write_method(call.method):
            raise MethodError(
                f"{call.method} is a write method; pass --allow-writes to run it anyway"
            )


def record_calls(
    workload: tuple[CallSpec, ...],
    *,
    samples: int = 1,
    config: BenchConfig | None = None,
    seed: int = 0,
    timeout: float = 10.0,
    budget: int = 32,
    client=None,
) -> tuple[CapturedCall, ...]:
    """Expand a mix into JSONL calls. Binds YAML sources when endpoints are given."""
    if samples < 1:
        raise CaptureError("samples must be at least 1")
    steps = workload
    if config is not None and has_dynamic_source(steps):
        purse = RequestBudget(budget)
        owns = client is None
        http = client or make_client(timeout=timeout)
        try:
            steps, _meta = _bind_from_chain(
                config,
                steps,
                seed=seed,
                timeout=timeout,
                purse=purse,
                deadline=None,
                concurrency=0,
                client=http,
            )
        finally:
            if owns:
                http.close()
    out: list[CapturedCall] = []
    for _kind, _index, spec in expand_steps(steps, warmup=0, samples=samples):
        out.append(CapturedCall(method=spec.method, params=tuple(spec.params)))
    if not out:
        raise CaptureError("workload produced no calls")
    return tuple(out)


def replay_calls(
    config: BenchConfig,
    calls: tuple[CapturedCall, ...],
    *,
    source: str = "capture.jsonl",
    timeout: float = 10.0,
    budget: int = 32,
    max_duration: float = 0.0,
    allow_writes: bool = False,
    client=None,
) -> ReplayResult:
    """Send each captured call to every provider in lockstep. Compare bodies."""
    reject_write_calls(calls, allow_writes=allow_writes)
    purse = RequestBudget(budget)
    deadline = None if max_duration <= 0 else time.monotonic() + max_duration
    owns = client is None
    http = client or make_client(timeout=timeout)
    try:
        steps: list[ReplayStep] = []
        by_name: dict[str, list[ReplayHit]] = {
            ep.name: [] for ep in config.endpoints
        }
        for index, call in enumerate(calls, start=1):
            raw_hits = _probe_wave(
                config,
                method=call.method,
                params=list(call.params),
                timeout=timeout,
                budget=purse,
                deadline=deadline,
                concurrency=0,
                client=http,
            )
            hits = tuple(
                _replay_hit(ep.name, raw_hits[ep.name]) for ep in config.endpoints
            )
            step = _assess_step(index, call, hits)
            steps.append(step)
            for hit in hits:
                by_name[hit.name].append(hit)
    finally:
        if owns:
            http.close()
    providers = tuple(
        _provider_summary(name, by_name[name], steps)
        for name in (ep.name for ep in config.endpoints)
    )
    return ReplayResult(
        source=source,
        calls=calls,
        steps=tuple(steps),
        providers=providers,
        timeout=timeout,
        budget=budget,
        budget_remaining=purse.remaining,
        allow_writes=allow_writes,
        git_sha=current_git_sha(),
        started_at=utc_stamp(),
        vantage=vantage_label(),
    )


def replay_to_dict(result: ReplayResult) -> dict[str, Any]:
    return {
        "tool": "rpcbench",
        "version": __version__,
        "schema": 1,
        "command": "replay",
        "watermark": {
            "version": __version__,
            "git_sha": result.git_sha,
            "utc": result.started_at,
            "family": result.family,
            "vantage": result.vantage,
            "methodology": DOCS_METHODOLOGY,
            "boundary": DOCS_BOUNDARY,
        },
        "source": result.source,
        "timeout": result.timeout,
        "budget": result.budget,
        "budget_remaining": result.budget_remaining,
        "allow_writes": result.allow_writes,
        "n": len(result.calls),
        "match": result.n_match,
        "mismatch": result.n_mismatch,
        "body_mismatch": result.n_body_mismatch,
        "status_mismatch": result.n_status_mismatch,
        "error_mismatch": result.n_error_mismatch,
        "providers": [
            {
                "name": row.name,
                "n_ok": row.n_ok,
                "n_fail": row.n_fail,
                "match": row.match,
                "mismatch": row.mismatch,
                "p95_ms": row.p95_ms,
                "error_rate": row.error_rate,
            }
            for row in result.providers
        ],
        "steps": [_step_json(step) for step in result.steps],
    }


def format_replay_json(result: ReplayResult) -> str:
    return json.dumps(replay_to_dict(result), indent=2, sort_keys=False) + "\n"


def format_replay(
    result: ReplayResult, *, verbose: bool = False, color: bool = False
) -> str:
    from rpcbench.report import _grid

    n = len(result.calls)
    names = ", ".join(row.name for row in result.providers)
    writes = "allowed" if result.allow_writes else "blocked"
    lines = [
        "RPCBench replay",
        "=" * 72,
        f"Capture   {n} calls from {result.source}",
        f"Endpoints {len(result.providers)} ({names})  ·  "
        f"Timeout {result.timeout:g}s  ·  "
        f"requests {result.budget} ({result.budget_remaining} left)  ·  "
        f"writes={writes}",
        f"Mode      lockstep  ·  {family_token(result.family, color=color)}",
        _cite(result, color=color),
        "",
        "Summary",
        f"  Match     {result.n_match}/{n}  (bodies among successes)",
        f"  Mismatch  {result.n_mismatch}/{n}  "
        f"bodies={result.n_body_mismatch}  "
        f"status={result.n_status_mismatch}  "
        f"error={result.n_error_mismatch}",
        "",
        "Calls  (lockstep; match = canonical body among who answered)",
    ]
    call_rows = []
    for step in result.steps:
        call_rows.append(
            [
                str(step.index),
                step.method,
                "yes" if step.match else "no",
                step.note,
            ]
        )
    lines.extend(
        _grid(
            ["#", "method", "match", "note"],
            call_rows,
            right=(True, False, False, False),
        )
    )
    lines.extend(["", "Providers"])
    prov_rows = []
    for row in result.providers:
        attempted = row.n_ok + row.n_fail
        err = "—" if row.error_rate is None else f"{row.error_rate * 100:.0f}%"
        p95 = "—" if row.p95_ms is None else f"{row.p95_ms:.1f}ms"
        prov_rows.append(
            [
                row.name,
                f"{row.n_ok}/{attempted}" if attempted else "—",
                err,
                f"{row.match}/{n}",
                p95,
            ]
        )
    lines.extend(
        _grid(
            ["name", "n", "err", "match", "p95"],
            prov_rows,
            right=(False, True, True, True, True),
        )
    )
    if verbose:
        diffs = _diff_lines(result)
        if diffs:
            lines.extend(["", "Diffs  (canonical JSON body; mismatched calls only)"])
            lines.extend(diffs)
    lines.append("")
    lines.append(
        f"{result.n_match} match  {result.n_mismatch} mismatch  ·  "
        "not mixed into ranking  ·  --verbose for body diffs"
    )
    _ = color
    return "\n".join(lines) + "\n"


def format_replay_md(result: ReplayResult) -> str:
    from rpcbench.markdown import _md_table

    data = replay_to_dict(result)
    n = data["n"]
    call_rows = [
        [
            str(step["index"]),
            str(step["method"]),
            "yes" if step["match"] else "no",
            str(step["note"] or "—"),
        ]
        for step in data["steps"]
    ]
    prov_rows = []
    for row in data["providers"]:
        attempted = (row["n_ok"] or 0) + (row["n_fail"] or 0)
        err = "—" if row["error_rate"] is None else f"{row['error_rate'] * 100:.0f}%"
        p95 = "—" if row["p95_ms"] is None else f"{row['p95_ms']:.1f}ms"
        prov_rows.append(
            [
                str(row["name"]),
                f"{row['n_ok']}/{attempted}" if attempted else "—",
                err,
                f"{row['match']}/{n}",
                p95,
            ]
        )
    lines = [
        "# RPCBench replay",
        "",
        f"{n} lockstep calls from `{result.source}`. "
        "Match is canonical body among who answered. Not mixed into ranking.",
        "",
        f"- match **{data['match']}/{n}**",
        f"- mismatch **{data['mismatch']}/{n}** "
        f"(bodies={data['body_mismatch']}, status={data['status_mismatch']}, "
        f"error={data['error_mismatch']})",
        "",
        "## Calls",
        "",
        *_md_table(
            ["#", "method", "match", "note"],
            call_rows,
            right=(True, False, False, False),
        ),
        "",
        "## Providers",
        "",
        *_md_table(
            ["name", "n", "err", "match", "p95"],
            prov_rows,
            right=(False, True, True, True, True),
        ),
        "",
    ]
    return "\n".join(lines)


REPLAY_COLUMNS = (
    "utc",
    "vantage",
    "source",
    "name",
    "n_ok",
    "n_fail",
    "match",
    "mismatch",
    "p95_ms",
    "error_rate",
)


def format_replay_csv(result: ReplayResult) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=REPLAY_COLUMNS)
    writer.writeheader()
    for row in result.providers:
        writer.writerow(
            {
                "utc": result.started_at or "",
                "vantage": result.vantage or "",
                "source": result.source,
                "name": row.name,
                "n_ok": str(row.n_ok),
                "n_fail": str(row.n_fail),
                "match": str(row.match),
                "mismatch": str(row.mismatch),
                "p95_ms": "" if row.p95_ms is None else f"{row.p95_ms:.6g}",
                "error_rate": "" if row.error_rate is None else f"{row.error_rate:.6g}",
            }
        )
    return buf.getvalue()


def format_replay_html(result: ReplayResult) -> str:
    from html import escape

    from rpcbench.html import _CSS

    data = replay_to_dict(result)
    n = data["n"]
    call_rows = []
    for step in data["steps"]:
        tone = "ok" if step["match"] else "bad"
        call_rows.append(
            "<tr>"
            f'<td class="num">{escape(str(step["index"]))}</td>'
            f'<td class="{tone}">{escape(str(step["method"]))}</td>'
            f'<td class="{tone}">{"yes" if step["match"] else "no"}</td>'
            f'<td>{escape(str(step["note"] or "—"))}</td>'
            "</tr>"
        )
    prov_rows = []
    for row in data["providers"]:
        attempted = (row["n_ok"] or 0) + (row["n_fail"] or 0)
        n_txt = f"{row['n_ok']}/{attempted}" if attempted else "—"
        match_txt = f"{row['match']}/{n}"
        err = "—" if row["error_rate"] is None else f"{row['error_rate'] * 100:.0f}%"
        p95 = "—" if row["p95_ms"] is None else f"{row['p95_ms']:.1f}ms"
        tone = "ok" if row["mismatch"] == 0 else "bad"
        prov_rows.append(
            "<tr>"
            f'<td class="{tone}">{escape(str(row["name"]))}</td>'
            f'<td class="num">{escape(n_txt)}</td>'
            f'<td class="num">{escape(err)}</td>'
            f'<td class="num">{escape(match_txt)}</td>'
            f'<td class="num">{escape(p95)}</td>'
            "</tr>"
        )
    utc = result.started_at or "—"
    sha = result.git_sha or "—"
    body = [
        "<h1>RPCBench replay</h1>",
        f'<p class="meta">{escape(str(n))} lockstep calls from '
        f'{escape(result.source)}; match is canonical body among who answered; '
        "not mixed into ranking</p>",
        "<h2>Summary</h2>",
        f"<p>match {data['match']}/{n} · mismatch {data['mismatch']}/{n} "
        f"(bodies={data['body_mismatch']}, status={data['status_mismatch']}, "
        f"error={data['error_mismatch']})</p>",
        '<section aria-label="calls"><h2>Calls</h2>'
        "<table><thead><tr>"
        '<th class="num">#</th><th>method</th><th>match</th><th>note</th>'
        "</tr></thead>"
        f"<tbody>{''.join(call_rows)}</tbody></table></section>",
        '<section aria-label="providers"><h2>Providers</h2>'
        "<table><thead><tr>"
        "<th>name</th>"
        '<th class="num">n</th><th class="num">err</th>'
        '<th class="num">match</th><th class="num">p95</th>'
        "</tr></thead>"
        f"<tbody>{''.join(prov_rows)}</tbody></table></section>",
        f"<footer>rpcbench {escape(__version__)} · sha={escape(str(sha))} · "
        f"{escape(str(utc))} · family={escape(result.family)} · "
        f'vantage={escape(str(result.vantage or "—"))} · '
        f'<a href="{DOCS_METHODOLOGY}">methodology</a> · '
        f'<a href="{DOCS_BOUNDARY}">boundary</a></footer>',
    ]
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8"/>\n'
        "<title>RPCBench · replay</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )


def body_diff_text(step: ReplayStep) -> str:
    """Unified diff of canonical JSON bodies. Empty when bodies match."""
    ok = [hit for hit in step.hits if hit.ok]
    if len(ok) < 2:
        return ""
    first = ok[0]
    other = next((hit for hit in ok[1:] if hit.body_hash != first.body_hash), None)
    if other is None:
        return ""
    left = pretty_json(first.result).splitlines()
    right = pretty_json(other.result).splitlines()
    diff = list(
        difflib.unified_diff(
            left,
            right,
            fromfile=first.name,
            tofile=other.name,
            lineterm="",
        )
    )
    text = "\n".join(diff)
    if len(text) > _DIFF_CHARS:
        return text[:_DIFF_CHARS] + "\n…"
    return text


def _assess_step(
    index: int, call: CapturedCall, hits: tuple[ReplayHit, ...]
) -> ReplayStep:
    oks = [hit.ok for hit in hits]
    status_mismatch = any(oks) and not all(oks)
    ok_hashes = [hit.body_hash for hit in hits if hit.ok and hit.body_hash]
    unique = set(ok_hashes)
    body_mismatch = len(unique) > 1
    fail_classes = [hit.error_class or "" for hit in hits if not hit.ok]
    error_mismatch = len(set(fail_classes)) > 1
    # Integrity is the body, among who answered. A down node is coverage, not a disagree.
    match = not body_mismatch
    canon = None
    if len(unique) == 1:
        canon = next(iter(unique))
    elif unique:
        counts: dict[str, int] = {}
        for digest in ok_hashes:
            counts[digest] = counts.get(digest, 0) + 1
        best = max(counts.values())
        winners = [k for k, n in counts.items() if n == best]
        if len(winners) == 1 and best > (len(ok_hashes) - best):
            canon = winners[0]
    return ReplayStep(
        index=index,
        method=call.method,
        params=call.params,
        hits=hits,
        match=match,
        body_mismatch=body_mismatch,
        status_mismatch=status_mismatch,
        error_mismatch=error_mismatch,
        canonical_hash=canon,
    )


def _replay_hit(name: str, hit: ProbeResult) -> ReplayHit:
    digest = hit.body_hash
    if hit.ok and digest is None and hit.result is not None:
        digest = body_hash(hit.result)
    return ReplayHit(
        name=name,
        ok=hit.ok,
        latency_ms=hit.latency_ms,
        body_hash=digest,
        error=hit.error,
        error_class=hit.error_class,
        result=hit.result,
    )


def _provider_summary(
    name: str, hits: list[ReplayHit], steps: list[ReplayStep]
) -> ReplayProvider:
    n_ok = sum(1 for hit in hits if hit.ok)
    n_fail = sum(1 for hit in hits if not hit.ok)
    attempted = n_ok + n_fail
    latencies = [hit.latency_ms for hit in hits if hit.ok and hit.latency_ms is not None]
    p95 = percentile(latencies, 0.95) if latencies else None
    match = 0
    for step in steps:
        mine = next((hit for hit in step.hits if hit.name == name), None)
        if mine is None or not mine.ok:
            continue
        if step.body_mismatch:
            if step.canonical_hash and mine.body_hash == step.canonical_hash:
                match += 1
            continue
        match += 1
    return ReplayProvider(
        name=name,
        n_ok=n_ok,
        n_fail=n_fail,
        match=match,
        mismatch=n_ok - match,
        p95_ms=p95,
        error_rate=(n_fail / attempted) if attempted else None,
    )


def _step_json(step: ReplayStep) -> dict[str, Any]:
    return {
        "index": step.index,
        "method": step.method,
        "params": list(step.params),
        "match": step.match,
        "body_mismatch": step.body_mismatch,
        "status_mismatch": step.status_mismatch,
        "error_mismatch": step.error_mismatch,
        "canonical_hash": step.canonical_hash,
        "note": None if step.note == "—" else step.note,
        "hits": [
            {
                "name": hit.name,
                "ok": hit.ok,
                "latency_ms": hit.latency_ms,
                "body_hash": hit.body_hash,
                "error": hit.error,
                "error_class": hit.error_class,
            }
            for hit in step.hits
        ],
    }


def _diff_lines(result: ReplayResult) -> list[str]:
    lines: list[str] = []
    for step in result.steps:
        if not step.body_mismatch:
            continue
        lines.append(f"  #{step.index}  {step.method}")
        for hit in step.hits:
            digest = hit.body_hash or "—"
            klass = hit.error_class or ("ok" if hit.ok else "fail")
            lines.append(f"    {hit.name}  {digest}  {klass}")
        blob = body_diff_text(step)
        if blob:
            for row in blob.splitlines():
                lines.append(f"    {row}")
    return lines


def _cite(result: ReplayResult, *, color: bool = False) -> str:
    sha = result.git_sha or "—"
    vantage = result.vantage or "—"
    utc = result.started_at or "—"
    family = family_token(result.family, color=color)
    return (
        f"Cite      {__version__}  sha={sha}  {family}  "
        f"vantage={vantage}  utc={utc}  ·  methodology · boundary"
    )


def _is_stream(source: object) -> bool:
    return hasattr(source, "read") and not isinstance(source, (str, Path))
