"""P3b: the Python CurvePlotter against GGB's real CurveSegmentPlotter path."""
from __future__ import annotations

import pytest

import ggb_curve_plotter_oracle
import ggb_oracle
from qmlmathplot.curveplotter import PlotView, plot_curve


def _view(data: dict) -> PlotView:
    view = data["view"]
    return PlotView(view["xmin"], view["xmax"], view["ymin"], view["ymax"],
                    view["width"], view["height"])


@pytest.mark.parametrize("name", ggb_curve_plotter_oracle.names())
def test_the_curve_plotter_matches_ggb(name: str) -> None:
    data = ggb_curve_plotter_oracle.load(name)
    assert data.get("error") is None, (name, data.get("error"))
    expression = ggb_oracle.sympy_expr(data["expression"])
    view = _view(data)
    actual = plot_curve(expression, view, view.xmin, view.xmax)
    expected = data["operations"]
    assert len(actual) == len(expected), name
    for index, (got, want) in enumerate(zip(actual, expected)):
        assert got[0] == want["op"], (name, index, got, want)
        if got[0] in {"MOVE_TO", "LINE_TO", "CORNER"}:
            assert got[1] == pytest.approx(want["x"], abs=1e-8), (name, index, got, want)
            assert got[2] == pytest.approx(want["y"], abs=1e-8), (name, index, got, want)
