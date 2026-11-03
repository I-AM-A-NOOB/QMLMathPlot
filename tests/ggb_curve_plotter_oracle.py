"""Load GGB's real CurvePlotter path for expressions the interval path rejects."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ggb_curve_plotter"


def names() -> list[str]:
    return sorted(path.stem for path in FIXTURE_DIR.glob("*.json"))


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
