"""Citable run stamp. Not a security score and not an SLA."""

from __future__ import annotations

import os
import socket
import subprocess
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rpcbench import __version__
from rpcbench.methods import is_app_workload

if TYPE_CHECKING:
    from rpcbench.run import RunResult

DOCS_METHODOLOGY = (
    "https://github.com/ehsanhajian/RPCBench/blob/main/docs/METHODOLOGY.md"
)
DOCS_BOUNDARY = (
    "https://github.com/ehsanhajian/RPCBench/blob/main/docs/BOUNDARY.md"
)
FAMILY_EVM = "evm"
# src/rpcbench/watermark.py → repo root for an editable checkout; site-packages otherwise.
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def git_sha() -> str | None:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            cwd=_PACKAGE_ROOT,
            text=True,
            timeout=1,
        )
        if head.returncode != 0:
            return None
        sha = head.stdout.strip()[:12]
        if not sha:
            return None
        dirty = subprocess.run(
            ["git", "diff", "--quiet"],
            check=False,
            capture_output=True,
            cwd=_PACKAGE_ROOT,
            timeout=1,
        )
        if dirty.returncode != 0:
            return f"{sha}-dirty"
        return sha
    except (OSError, subprocess.TimeoutExpired):
        return None


def utc_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def vantage_label() -> str:
    env = os.environ.get("RPCBENCH_VANTAGE", "").strip()
    if env:
        return env
    try:
        return socket.gethostname() or "local"
    except OSError:
        return "local"


def workload_label(result: RunResult) -> str:
    if is_app_workload(result.profile):
        return result.profile
    return result.method


def as_dict(result: RunResult) -> dict[str, Any]:
    return {
        "version": __version__,
        "git_sha": result.git_sha,
        "utc": result.started_at,
        "budget": result.sample_budget,
        "workload": workload_label(result),
        "seed": result.seed,
        "family": result.family,
        "vantage": result.vantage,
        "samples": result.samples,
        "warmup": result.warmup,
        "methodology": DOCS_METHODOLOGY,
        "boundary": DOCS_BOUNDARY,
    }


def html_footer(result: RunResult) -> str:
    """Footer fragment for a later HTML report. Same links as JSON."""
    mark = as_dict(result)
    utc = mark["utc"] or "—"
    sha = mark["git_sha"] or "—"
    return (
        f'<footer>rpcbench {escape(str(mark["version"]))} · sha={escape(str(sha))} · '
        f'{escape(str(utc))} · family={escape(str(mark["family"]))} · '
        f'vantage={escape(str(mark["vantage"] or "—"))} · '
        f'<a href="{DOCS_METHODOLOGY}">methodology</a> · '
        f'<a href="{DOCS_BOUNDARY}">boundary</a></footer>'
    )


def cite_line(result: RunResult) -> str:
    mark = as_dict(result)
    sha = mark["git_sha"] or "—"
    vantage = mark["vantage"] or "—"
    utc = mark["utc"] or "—"
    return (
        f"Cite      {mark['version']}  sha={sha}  family={mark['family']}  "
        f"vantage={vantage}  utc={utc}  ·  methodology · boundary"
    )
