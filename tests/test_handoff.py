"""Hand-off tests: the matplotlib figure, the sympy plot, and the optional-dependency rule.

matplotlib is an *optional* extra, so the tests that need it skip when it is missing, the
missing-extra error is provoked by hiding the module rather than uninstalling it, and one test
runs in a fresh interpreter to prove that ``import qmlmathplot`` does not import matplotlib.
"""

from __future__ import annotations

import subprocess
import sys

import numpy as np
import pytest

import qmlmathplot
from matplotlib.colors import to_rgba

from qmlmathplot import Plot

#: The hand-off is a *sampled* renderer; the tests check the mapping, not the pixels.
pytest.importorskip("matplotlib", reason="the hand-off needs the matplotlib extra")


def _plot(*, aspect: str | float = "auto", position: str = "zero") -> Plot:
    plot = Plot()
    plot.line_width = 2.0
    plot.camera.setViewport(800.0, 600.0)
    plot.camera.aspect = aspect
    plot.add_curve("sin(1/x)", color="#1f77b4", label="sin(1/x)")
    plot.axes_position = position
    return plot


def test_importing_the_package_does_not_import_matplotlib() -> None:
    """The extra stays optional: nothing in the package may pull matplotlib in at import."""
    script = "import qmlmathplot, sys; print('matplotlib' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", "importing qmlmathplot imported matplotlib"


def test_missing_matplotlib_names_the_extra(monkeypatch) -> None:
    """Hiding the module is how the missing extra is tested (no uninstalling)."""
    monkeypatch.setitem(sys.modules, "matplotlib", None)      # `import matplotlib` now raises
    plot = _plot()
    with pytest.raises(ImportError, match=r"qmlmathplot\[matplotlib\]"):
        plot.to_matplotlib()
    with pytest.raises(ImportError, match=r"qmlmathplot\[matplotlib\]"):
        plot.to_sympy()


def test_figure_reflects_limits_style_and_aspect() -> None:
    plot = _plot(aspect=0.5)
    plot.background = "#101010"
    plot.grid = True
    plot.grid_color = "#202020"
    plot.grid_width = 1.5
    plot.grid_style = "--"
    plot.title = "QMLMathPlot"
    plot.title_bold = False
    plot.tick_color = "#303030"
    plot.text_color = "#404040"
    plot.tick_font_size = 12.0
    plot.axis_color = "#505050"
    plot.axis_width = 2.0
    plot.add_curve("sin(x)", color="#ff7f0e", line_width=3.0)

    figure = plot.to_matplotlib(xlim=(-2, 2), ylim=(-1, 1), samples=101)
    assert len(figure.axes) == 1, "the hand-off is one Axes"
    axes = figure.axes[0]
    assert tuple(float(value) for value in axes.get_xlim()) == (-2.0, 2.0)
    assert tuple(float(value) for value in axes.get_ylim()) == (-1.0, 1.0)
    assert float(axes.get_aspect()) == 0.5                    # same convention as the camera
    assert axes.get_adjustable() == "box"
    assert axes.get_facecolor()[:3] == pytest.approx((0x10 / 255, 0x10 / 255, 0x10 / 255))
    assert axes.get_title() == "QMLMathPlot"
    assert axes.get_axisbelow() is True, "our grid is painted below the curves"
    assert [line.get_label() for line in axes.lines] == ["sin(1/x)", "sin(x)"]
    assert len(axes.lines[0].get_xdata()) == 101              # `samples` points per curve
    # the grid style is mapped (Colour/width/alpha/style) and the grid is on
    gridline = axes.xaxis.get_gridlines()[0]
    assert gridline.get_linestyle() == "--"
    assert gridline.get_color() == "#202020"
    assert gridline.get_linewidth() == pytest.approx(1.5 * 72 / 96)
    # "zero" puts the spines through the origin and hides the box's other two
    assert axes.spines["left"].get_position() == "zero"
    assert axes.spines["bottom"].get_position() == "zero"
    assert not axes.spines["top"].get_visible() and not axes.spines["right"].get_visible()
    assert axes.spines["left"].get_edgecolor() == to_rgba("#505050")


def test_grid_off_is_not_turned_on_by_the_line_properties() -> None:
    """Passing line properties together with ``False`` would *enable* matplotlib's grid."""
    plot = _plot()
    plot.grid = False
    axes = plot.to_matplotlib().axes[0]
    assert not any(line.get_visible() for line in axes.xaxis.get_gridlines())


def test_axes_position_edge_keeps_the_box() -> None:
    axes = _plot(position="edge").to_matplotlib().axes[0]
    assert axes.spines["left"].get_position() != "zero"
    assert axes.spines["top"].get_visible() and axes.spines["right"].get_visible()


def test_hidden_curves_are_not_handed_over() -> None:
    plot = _plot()
    extra = plot.add_curve("cos(x)")
    assert len(plot.to_matplotlib().axes[0].lines) == 2
    extra.visible = False
    lines = plot.to_matplotlib().axes[0].lines
    assert [line.get_label() for line in lines] == ["sin(1/x)"]


def test_sampling_puts_nan_outside_the_domain() -> None:
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.add_curve("log(x)")
    plot.add_curve("0.5 + 0*x")                               # a constant: no NaN anywhere
    axes = plot.to_matplotlib(xlim=(-1, 1), samples=50).axes[0]

    xs = np.asarray(axes.lines[0].get_xdata(), dtype=float)
    ys = np.asarray(axes.lines[0].get_ydata(), dtype=float)
    assert np.isnan(ys[xs < 0]).all(), "log(x) must be NaN for x < 0 (matplotlib breaks there)"
    assert np.isfinite(ys[xs > 0]).all()
    assert np.isfinite(np.asarray(axes.lines[1].get_ydata(), dtype=float)).all()


def test_adjustable_and_aspect_follow_the_export_modes() -> None:
    plot = _plot(aspect=1.0)
    assert plot.to_matplotlib(adjustable="datalim").axes[0].get_adjustable() == "datalim"
    stretch = plot.to_matplotlib(adjustable="stretch").axes[0]
    assert stretch.get_aspect() == "auto", "stretch means independent scales"
    with pytest.raises(ValueError):
        plot.to_matplotlib(adjustable="crop")
    with pytest.raises(ValueError):
        plot.to_matplotlib(xlim=(1, -1))
    with pytest.raises(ValueError):
        plot.to_matplotlib(adjustable="box", ylim=(0.0, 0.0))


def test_savefig_matplotlib_backend_writes_vector_output(tmp_path) -> None:
    plot = _plot()
    svg = tmp_path / "handoff.svg"
    plot.savefig(str(svg), backend="matplotlib", xlim=(-2, 2), ylim=(-1, 1))
    assert svg.exists() and svg.stat().st_size > 0
    assert "<svg" in svg.read_text(encoding="utf-8")

    pdf = tmp_path / "handoff.pdf"
    plot.savefig(str(pdf), backend="matplotlib")
    assert pdf.read_bytes()[:4] == b"%PDF"

    png = tmp_path / "handoff.png"
    plot.savefig(str(png), backend="matplotlib", width=600, height=400, dpi=100)
    assert png.stat().st_size > 0


def test_savefig_validates_the_backend(tmp_path) -> None:
    plot = _plot()
    with pytest.raises(ValueError, match="backend"):
        plot.savefig(str(tmp_path / "x.png"), backend="sympy")


def test_to_sympy_returns_a_plot_whose_save_writes_a_file(tmp_path) -> None:
    plot = _plot()
    plot.add_curve("sin(x)", color="#ff7f0e", label="sin(x)")
    handle = plot.to_sympy(xlim=(-2, 2), ylim=(-1, 1), samples=120)
    assert hasattr(handle, "save") and hasattr(handle, "show")
    assert handle[0].line_color == "#1f77b4", "each curve keeps its colour"
    assert handle[1].line_color == "#ff7f0e"
    assert len(handle[0].get_data()[0]) == 120, "our sampling count, not sympy's adaptive one"
    target = tmp_path / "sympy.png"
    handle.save(str(target))
    assert target.exists() and target.stat().st_size > 0
    # the window the hand-off asked for is the one sympy shows
    assert tuple(float(value) for value in handle.ax.get_ylim()) == (-1.0, 1.0)


def test_to_sympy_needs_a_visible_curve() -> None:
    plot = Plot()
    with pytest.raises(ValueError, match="no visible curve"):
        plot.to_sympy()


def test_the_hand_off_is_reached_through_the_plot() -> None:
    """`mpl` is an implementation module, not a re-exported API: the entry points are methods
    (`test_importing_the_package_does_not_import_matplotlib` covers the lazy import)."""
    for method in ("to_matplotlib", "to_sympy", "to_image"):
        assert callable(getattr(Plot(), method))
    assert callable(qmlmathplot.Plot.savefig)
