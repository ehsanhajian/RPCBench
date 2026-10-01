"""Live terminal progress during a compare. No third-party TUI deps."""

from __future__ import annotations

import math
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, TextIO

from rpcbench.rpc import ProbeResult

_SPARK = "▁▂▃▄▅▆▇█"
_SPARK_N = 24


@dataclass
class ProviderLive:
    name: str
    ok_ms: list[float] = field(default_factory=list)
    n_fail: int = 0
    last_error: str | None = None

    @property
    def n_ok(self) -> int:
        return len(self.ok_ms)

    @property
    def n(self) -> int:
        return self.n_ok + self.n_fail

    def record(self, hit: ProbeResult) -> None:
        if hit.ok and hit.latency_ms is not None:
            self.ok_ms.append(float(hit.latency_ms))
        else:
            self.n_fail += 1
            self.last_error = hit.error_class or hit.error

    def p50(self) -> float | None:
        return _percentile(self.ok_ms, 0.50)

    def p95(self) -> float | None:
        return _percentile(self.ok_ms, 0.95)

    def error_rate(self) -> float | None:
        if self.n == 0:
            return None
        return self.n_fail / self.n

    def rps(self) -> float | None:
        mean = _mean(self.ok_ms)
        if mean is None or mean <= 0:
            return None
        return 1000.0 / mean

    def spark(self) -> str:
        window = self.ok_ms[-_SPARK_N:]
        if not window:
            return "—"
        lo = min(window)
        hi = max(window)
        if hi <= lo:
            return _SPARK[0] * len(window)
        out: list[str] = []
        for value in window:
            idx = int((value - lo) / (hi - lo) * (len(_SPARK) - 1))
            out.append(_SPARK[max(0, min(len(_SPARK) - 1, idx))])
        return "".join(out)


class LiveTui:
    """Redraw a compact per-provider table on a TTY (stderr when stdout is JSON)."""

    def __init__(
        self,
        names: list[str],
        *,
        stream: TextIO | None = None,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.stream = stream or sys.stderr
        self.providers = {name: ProviderLive(name=name) for name in names}
        self.order = list(names)
        self.phase = "starting"
        self._lines = 0
        self._lock = threading.Lock()
        self.aborted = False

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self.phase = phase
            self._draw()

    def record(self, name: str, hit: ProbeResult, *, kind: str = "sample") -> None:
        with self._lock:
            row = self.providers.get(name)
            if row is None:
                row = ProviderLive(name=name)
                self.providers[name] = row
                self.order.append(name)
            if kind != "warmup":
                row.record(hit)
            self.phase = kind
            self._draw()

    def finish(self, *, aborted: bool = False) -> None:
        with self._lock:
            self.aborted = aborted
            if not self.enabled:
                return
            self.phase = "aborted" if aborted else "done"
            self._draw()
            # Leave the final frame; do not wipe — user sees last stats.
            self._lines = 0

    def _draw(self) -> None:
        if not self.enabled:
            return
        lines = self._render_lines()
        # Move up previous frame, then rewrite.
        if self._lines:
            self.stream.write(f"\033[{self._lines}A")
        for line in lines:
            self.stream.write(f"\033[2K{line}\n")
        # Clear leftover lines if the new frame is shorter.
        for _ in range(max(0, self._lines - len(lines))):
            self.stream.write("\033[2K\n")
        if self._lines > len(lines):
            self.stream.write(f"\033[{self._lines - len(lines)}A")
        self.stream.flush()
        self._lines = len(lines)

    def _render_lines(self) -> list[str]:
        header = f"rpcbench  live  ·  {self.phase}"
        if self.aborted:
            header += "  ·  aborted (flushing report)"
        rows = [
            f"{'provider':<16}{'n':>5}{'p50':>8}{'p95':>8}{'err':>7}{'rps':>8}  spark"
        ]
        for name in self.order:
            row = self.providers[name]
            rows.append(
                f"{name:<16}{row.n:>5}"
                f"{_ms(row.p50()):>8}{_ms(row.p95()):>8}"
                f"{_err(row.error_rate()):>7}{_rps(row.rps()):>8}  {row.spark()}"
            )
        return [header, *rows]


def wants_tui(*, plain: bool, ci: bool, stdout: TextIO | None = None) -> bool:
    """TTY live UI unless --plain / --ci or stdout is not interactive."""
    if plain or ci:
        return False
    stream = stdout or sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def install_abort_flag() -> tuple[threading.Event, Any]:
    """First Ctrl-C sets the flag; second exits. Returns (flag, previous SIGINT handler)."""
    import signal

    flag = threading.Event()
    hits = {"n": 0}
    previous = signal.getsignal(signal.SIGINT)

    def _handler(signum: int, frame: Any) -> None:
        del signum, frame
        hits["n"] += 1
        flag.set()
        if hits["n"] >= 2:
            raise SystemExit(130)

    signal.signal(signal.SIGINT, _handler)
    return flag, previous


def restore_sigint(previous: Any) -> None:
    import signal

    signal.signal(signal.SIGINT, previous)


def _percentile(samples: list[float], p: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    rank = max(1, int(round(p * len(ordered))))
    return ordered[min(rank, len(ordered)) - 1]


def _mean(samples: list[float]) -> float | None:
    if not samples:
        return None
    return sum(samples) / len(samples)


def _ms(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 100:
        return f"{value:.0f}"
    return f"{value:.1f}"


def _err(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{100 * value:.0f}%"


def _rps(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    if value >= 100:
        return f"{value:.0f}"
    return f"{value:.1f}"
