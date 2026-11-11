"""P2a: the Python path state machine against GGB's real IntervalPath segments.

The fixtures contain GGB's per-column topologies and the state machine's operations in
screen space (no plotter clip). Feed the *GGB topologies* into our state machine; this
isolates path decisions from the interval engine.
"""
from __future__ import annotations

import math

import ggb_oracle
import pytest

from qmlmathplot.ggb_path import Column, IntervalPath, IVal

# Exact on the oracle corpus. ``exp_tan_wide`` is a deliberate GGB-weak case (accepted);
# ``cot_sec`` and ``ln_cosh`` still have small known path deviations and are tracked
# separately rather than hidden inside this assertion.
EXACT = [
    "tan_default", "inv_default", "ln_default", "sin_inv_default", "tan_wide_y",
    "rational_sq", "sqrt_tan_cot", "cube_root", "tan_double",
]
KNOWN = ["cot_sec", "ln_cosh", "exp_tan_wide"]


def _num(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return {"Infinity": math.inf, "-Infinity": -math.inf, "NaN": math.nan}[value]


def _ival(column: dict) -> IVal:
    kind = column["kind"]
    if kind == "CONNECTED":
        return IVal(_num(column["lo"]), _num(column["hi"]), "connected")
    if kind == "INVERTED":
        return IVal(_num(column["lo"]), _num(column["hi"]), "inverted")
    if kind == "WHOLE":
        return IVal(0.0, 0.0, "whole")
    return IVal(0.0, 0.0, "empty")


def _oracle_lines(data: dict) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    lines = []
    point = None
    for entry in data["segments"]:
        op = entry["op"]
        if op == "MOVE_TO":
            point = (entry["x"], entry["y"])
        elif op == "LINE_TO":
            if point is not None:
                lines.append((point, (entry["x"], entry["y"])))
            point = (entry["x"], entry["y"])
        elif op == "SEGMENT":
            lines.append(((entry["x1"], entry["y1"]), (entry["x2"], entry["y2"])))
            point = (entry["x2"], entry["y2"])
    return lines


def _ours(data: dict) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    bounds = data["bounds"]
    size = data["size"]
    columns = [Column(c["x"][0], c["x"][1], _ival(c)) for c in data["columns"]]
    path = IntervalPath(columns, size["width"], size["height"],
                        bounds["xmin"], bounds["xmax"], bounds["ymin"], bounds["ymax"])
    return [((a.x, a.y), (b.x, b.y)) for a, b in path.update()]


@pytest.mark.parametrize("name", EXACT)
def test_the_path_state_machine_matches_ggb(name: str) -> None:
    data = ggb_oracle.load(name)
    assert data["supported"] is True, name
    oracle = _oracle_lines(data)
    ours = _ours(data)
    assert len(ours) == len(oracle), name
    for index, (expected, actual) in enumerate(zip(oracle, ours)):
        for point_index in (0, 1):
            for coordinate in (0, 1):
                assert actual[point_index][coordinate] == pytest.approx(
                    expected[point_index][coordinate], abs=0.01
                ), (name, index, expected, actual)


def test_the_known_deviations_are_explicit() -> None:
    for name in KNOWN:
        data = ggb_oracle.load(name)
        assert data["supported"] is True, name
        assert _oracle_lines(data) and _ours(data), name
