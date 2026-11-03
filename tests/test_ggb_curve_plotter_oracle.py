"""P3a: GGB CurvePlotter fixtures for the expressions the interval gate rejects."""
from __future__ import annotations

import ggb_curve_plotter_oracle


def test_the_unsupported_corpus_has_curve_plotter_paths() -> None:
    names = set(ggb_curve_plotter_oracle.names())
    assert {"curve_mixed_default", "curve_mixed_wide", "curve_rational",
            "curve_dependent", "curve_floor", "curve_sign"} <= names
    for name in names:
        data = ggb_curve_plotter_oracle.load(name)
        assert data.get("error") is None, (name, data.get("error"))
        assert data["operations"], name
        ops = {entry["op"] for entry in data["operations"]}
        assert ops <= {"MOVE_TO", "LINE_TO", "CORNER", "END"}, (name, ops)
        assert "MOVE_TO" in ops or "LINE_TO" in ops, name


def test_the_problem_expression_uses_the_sampled_curve_plotter() -> None:
    data = ggb_curve_plotter_oracle.load("curve_mixed_wide")
    assert data["expression"].startswith("-x^2/2 + x*tan(x)")
    assert data["view"] == {"xmin": -122.7308, "xmax": 83.633,
                            "ymin": -75.9195, "ymax": 83.5434,
                            "width": 800, "height": 600}
    # CurvePlotter records the curve in *world* coordinates, before the view maps it.
    xs = [entry["x"] for entry in data["operations"] if "x" in entry]
    assert xs and min(xs) >= data["view"]["xmin"] - 1e-6
    assert max(xs) <= data["view"]["xmax"] + 1e-6
    assert any(entry["op"] == "END" for entry in data["operations"])
