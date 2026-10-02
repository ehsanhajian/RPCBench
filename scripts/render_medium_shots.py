"""Render Medium article screenshots (SVG + PNG).

    PYTHONPATH=src .venv/bin/python scripts/render_medium_shots.py

Outputs under docs/images/medium/ as medium_*.{svg,png}.
"""

from __future__ import annotations

import html
from pathlib import Path

import cairosvg

from render_cli_shots import (
    _FONT,
    _BG,
    _BAR,
    _DIM,
    _FG,
    _ACCENT,
    _GREEN,
    _RED,
    _AMBER,
    cold_result,
    demo_result,
    format_run,
    mix_result,
    render_html_shot,
    render_svg,
    shot_svgs,
    _extract,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images" / "medium"


def _code_card(title: str, body: str, *, width: int = 820) -> str:
    lines = body.strip("\n").splitlines() or [""]
    line_h = 22
    pad_x = 22
    pad_y = 18
    bar_h = 36
    height = bar_h + pad_y * 2 + line_h * len(lines) + 8
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">',
        f'<rect width="{width}" height="{height}" rx="10" fill="{_BG}"/>',
        f'<rect width="{width}" height="{bar_h}" rx="10" fill="{_BAR}"/>',
        f'<rect y="{bar_h - 10}" width="{width}" height="10" fill="{_BAR}"/>',
        '<circle cx="18" cy="18" r="5" fill="#ff5f57"/>',
        '<circle cx="36" cy="18" r="5" fill="#febc2e"/>',
        '<circle cx="54" cy="18" r="5" fill="#28c840"/>',
        f'<text x="{width / 2}" y="23" text-anchor="middle" fill="{_DIM}" '
        f'font-family="{_FONT}" font-size="12">{html.escape(title)}</text>',
    ]
    y = bar_h + pad_y + 14
    for line in lines:
        # Light syntax tint for comments / flags
        fill = _DIM if line.strip().startswith("#") else _FG
        if "uses:" in line or "rpcbench" in line.lower():
            fill = _ACCENT if "uses:" in line else _FG
        parts.append(
            f'<text x="{pad_x}" y="{y}" fill="{fill}" font-family="{_FONT}" '
            f'font-size="14" xml:space="preserve">{html.escape(line)}</text>'
        )
        y += line_h
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _ranking_focus(text: str) -> str:
    """Keep Summary through Ranking (drop long Notes/Batch tails)."""
    start = text.find("Summary")
    if start < 0:
        return text
    end = text.find("\nNotes")
    if end < 0:
        end = text.find("\nBatch")
    if end < 0:
        end = len(text)
    return text[start:end].rstrip() + "\n"


def medium_svgs() -> dict[str, str]:
    compare = demo_result()
    mix = mix_result()
    cold = cold_result()
    compact = format_run(compare, verbose=False, color=True)
    mix_txt = format_run(mix, verbose=False, color=True)
    verbose = format_run(compare, verbose=True, color=True)
    timing = format_run(cold, verbose=True, color=True)

    return {
        # Hero: full compact compare
        "medium_01_cli-compare.svg": render_svg(
            compact,
            "rpcbench compare --endpoints endpoints.yaml",
        ),
        # P95 / ranking focus for the percentile section
        "medium_02_ranking-p95.svg": render_svg(
            _ranking_focus(compact),
            "Default ranking · P95 · similar-band",
        ),
        # Verdict + route called out in article
        "medium_03_verdict-route.svg": render_svg(
            compact,
            "Verdict + Route (primary / fallback)",
        ),
        # Workload mix / coverage
        "medium_04_workload-mix.svg": render_svg(
            mix_txt,
            "rpcbench compare --workload wallet --budget short",
        ),
        # HTML report
        "medium_05_html-report.svg": render_html_shot(
            compare,
            "rpcbench compare --html -o report.html",
        ),
        # Heatmap for coverage
        "medium_06_heatmap.svg": render_html_shot(
            mix,
            "Coverage heatmap · this workload only",
            max_signals=1,
        ),
        # HTTP timing for "not just a ping"
        "medium_07_http-timing.svg": render_svg(
            _extract(timing, "Timing  (", "\nProviders"),
            "rpcbench compare --new-connection --verbose",
        ),
        # Verbose signals / reliability for "fast ≠ healthy"
        "medium_08_verbose-signals.svg": render_svg(
            _extract(verbose, "Reliability", "\nProviders")
            if "Reliability" in verbose
            else _extract(verbose, "Signals", "\nProviders"),
            "rpcbench compare --verbose",
        ),
        # CI Action snippet
        "medium_09_github-action.svg": _code_card(
            "GitHub Action · SLO gate",
            """
- uses: ehsanhajian/RPCBench@v0.6.1
  with:
    endpoints: endpoints.yaml
    budget: short
    max-p95: "500"
    max-error-rate: "0.05"
# Fail the job when an SLO budget misses.
# Artifacts: report.html / report.json / report.md
""".strip(
                "\n"
            ),
        ),
        # Quick start commands
        "medium_10_quickstart.svg": _code_card(
            "Quick start",
            """
pip install rpcbench

rpcbench compare --endpoints endpoints.yaml
rpcbench compare --endpoints endpoints.yaml --workload wallet
rpcbench compare --endpoints endpoints.yaml --html -o report.html
rpcbench compare --endpoints endpoints.yaml --out-dir out --ci --max-p95 500
""".strip(
                "\n"
            ),
            width=900,
        ),
        # Multi-region vantage
        "medium_11_vantage-merge.svg": _code_card(
            "One machine is one vantage",
            """
RPCBENCH_VANTAGE=eu-west \\
  rpcbench compare --endpoints endpoints.yaml --seed 7 -o eu.json

RPCBENCH_VANTAGE=us-east \\
  rpcbench compare --endpoints endpoints.yaml --seed 7 -o us.json

rpcbench merge eu.json us.json
""".strip(
                "\n"
            ),
            width=900,
        ),
        # Boundary / three tools
        "medium_12_boundary.svg": _code_card(
            "Three tools · three questions",
            """
Nodeprobe        Is this RPC safe to expose?
RPCBench         Which RPC performs best for this workload?
ValidatorPulse   Is my validator healthy?

# Overlap at the protocol. Different questions.
""".strip(
                "\n"
            ),
            width=900,
        ),
    }


def write_shots(out: Path = OUT) -> None:
    out.mkdir(parents=True, exist_ok=True)
    captions = {
        "medium_01_cli-compare": "Default compact CLI: Summary, Verdict, Route, Ranking",
        "medium_02_ranking-p95": "P95 ranking with similar-band co-winners",
        "medium_03_verdict-route": "ready / risky / not ready + primary/fallback route",
        "medium_04_workload-mix": "Workload mix (wallet/indexer-style coverage)",
        "medium_05_html-report": "Standalone HTML report (offline charts)",
        "medium_06_heatmap": "Provider × method coverage heatmap",
        "medium_07_http-timing": "HTTP timing split (handshake / server / payload)",
        "medium_08_verbose-signals": "Verbose reliability + signals",
        "medium_09_github-action": "CI Action with SLO budgets",
        "medium_10_quickstart": "Install and first compares",
        "medium_11_vantage-merge": "Multi-region vantage + merge",
        "medium_12_boundary": "RPCBench vs Nodeprobe vs ValidatorPulse",
    }
    index_lines = [
        "# Medium article screenshots",
        "",
        "Generated by `scripts/render_medium_shots.py`.",
        "",
        "Raw PNG (best for Medium embeds):",
        "",
    ]
    for name, svg in medium_svgs().items():
        stem = name.removesuffix(".svg")
        svg_path = out / name
        png_path = out / f"{stem}.png"
        svg_path.write_text(svg, encoding="utf-8")
        cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            write_to=str(png_path),
            output_width=1400,
        )
        caption = captions.get(stem, stem)
        print(f"{png_path.name}  ({png_path.stat().st_size // 1024} KiB)  {caption}")
        pages = f"https://ehsanhajian.github.io/RPCBench/images/medium/{stem}.png"
        raw = (
            "https://raw.githubusercontent.com/ehsanhajian/RPCBench/main/"
            f"docs/images/medium/{stem}.png"
        )
        index_lines.extend(
            [
                f"## {stem}",
                "",
                caption,
                "",
                f"- Pages: `{pages}`",
                f"- Raw: `{raw}`",
                "",
                f"![{caption}]({stem}.png)",
                "",
            ]
        )
    (out / "README.md").write_text("\n".join(index_lines), encoding="utf-8")
    print(f"index → {out / 'README.md'}")


if __name__ == "__main__":
    # Allow `python scripts/render_medium_shots.py` from repo root.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    write_shots()
