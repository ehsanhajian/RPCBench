from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_cli_shots.py"


def _shots():
    spec = importlib.util.spec_from_file_location("render_cli_shots", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_readme_cli_shots_match_renderer() -> None:
    mod = _shots()
    expected = mod.shot_svgs()
    assert expected
    on_disk = {path.name for path in mod.OUT.glob("cli-*.svg")}
    assert on_disk == set(expected), (
        "docs/images/cli-*.svg does not match scripts/render_cli_shots.py. "
        "Run: PYTHONPATH=src python scripts/render_cli_shots.py"
    )
    for name, svg in expected.items():
        committed = (mod.OUT / name).read_text(encoding="utf-8")
        assert committed == svg, (
            f"{name} is stale. Run: PYTHONPATH=src python scripts/render_cli_shots.py"
        )
