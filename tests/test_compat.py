"""Tests for the Matplotlib-shaped facade: ``Figure`` / ``Axes`` / ``FigureCanvasQt`` / plt.

The facade is intentionally thin: these tests pin the vocabulary that maps (limits, title,
labels, grid, aspect, savefig) and the places where Matplotlib's mechanism is *not* copied
(x/y arrays, subplot grids, legends, vector output).
"""

from __future__ import annotations

import subprocess
import sys

import pytest
import sympy as sp
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from qmlmathplot import Figure, FigureCanvasQt, MathPlotWidget, Plot
from qmlmathplot import pyplot as plt

X = sp.Symbol("x")


@pytest.fixture(autouse=True)
def _clear_pyplot():
    plt.close("all")
    yield
    plt.close("all")


def test_axes_plot_accepts_an_eq_level_set():
    x, y = sp.symbols("x y")
    figure = Figure()
    curve = figure.gca().plot(sp.Eq(x**2 + y**2, 1))[0]
    assert curve.implicit is True
    assert curve.expr == x**2 + y**2 - 1
    assert curve.expression == "Eq(x**2 + y**2, 1)"


def test_axes_plot_takes_expressions_and_styles():
    figure = Figure()
    axes = figure.gca()
    curves = axes.plot("sin(x)", "cos(x)")
    assert [curve.expression for curve in curves] == ["sin(x)", "cos(x)"]
    assert len(figure.plot.curves) == 2

    curve = axes.plot(sp.sin(X), color="#ff0000", linewidth=3.0, label="s",
                      visible=False)[0]
    assert curve.expr == sp.sin(X)
    assert curve.color.name() == "#ff0000"
    assert curve.lineWidth == pytest.approx(3.0)
    assert curve.label == "s"
    assert curve.visible is False

    more = axes.plot(["x", "x**2"])
    assert [curve.expression for curve in more] == ["x", "x**2"]


def test_data_arrays_are_refused_with_an_explanation():
    axes = Figure().gca()
    with pytest.raises(NotImplementedError, match="expression"):
        axes.plot([1.0, 2.0], [3.0, 4.0])
    with pytest.raises(TypeError):
        axes.plot()


def test_limits_title_labels_grid_and_aspect():
    figure = Figure()
    axes = figure.gca()
    axes.set_xlim(-1.0, 1.0)
    axes.set_ylim(bottom=-2.0, top=2.0)
    axes.set_title("title", color="#112233", fontsize=22, fontweight="bold")
    axes.set_xlabel("x")
    axes.set_ylabel("y")
    axes.grid(True, color="#ff0000", linewidth=2.0, alpha=0.5, linestyle=":")
    axes.set_aspect("equal")
    axes.set_facecolor("#eeeeee")

    assert axes.get_xlim() == pytest.approx((-3.0, 3.0))       # aspect expanded, never cropped
    assert axes.get_ylim() == pytest.approx((-2.0, 2.0))
    assert figure.plot.title == "title"
    assert figure.plot.title_color.name() == "#112233"
    assert figure.plot.title_font_size == pytest.approx(22.0)
    assert figure.plot.title_bold is True
    assert figure.plot.xlabel == "x" and figure.plot.ylabel == "y"
    assert figure.plot.grid is True
    assert figure.plot.grid_color.name() == "#ff0000"
    assert figure.plot.grid_width == pytest.approx(2.0)
    assert figure.plot.grid_alpha == pytest.approx(0.5)
    assert figure.plot.grid_style == ":"
    assert figure.plot.camera.aspect == 1.0
    assert figure.plot.background.name() == "#eeeeee"

    axes.set_xlim((0.0, 10.0))
    assert axes.get_xlim() == pytest.approx((0.0, 10.0))


def test_one_view_per_figure():
    figure = Figure()
    assert figure.add_subplot(111) is figure.gca()
    assert figure.add_subplot(1, 1, 1) is figure.gca()
    assert figure.add_subplot() is figure.gca()
    with pytest.raises(NotImplementedError, match="Qt layout"):
        figure.add_subplot(211)
    with pytest.raises(NotImplementedError, match="Qt layout"):
        plt.subplots(2, 1)
    created, axes = plt.subplots()
    assert created.gca() is axes


def test_pyplot_current_figure_and_plot():
    first = plt.figure()
    second = plt.figure(7)
    assert second.number == 7
    assert plt.gcf() is second
    plt.sca(first.gca())
    assert plt.gcf() is first

    curves = plt.plot("sin(x)")
    assert curves[0].expression == "sin(x)"
    assert plt.gca() is first.gca()
    plt.xlim(-2, 2)
    plt.ylim(-3, 3)
    plt.title("t")
    plt.xlabel("x")
    plt.ylabel("y")
    plt.grid(True)
    plt.aspect("equal")
    assert first.plot.title == "t"
    assert first.plot.xlabel == "x" and first.plot.ylabel == "y"
    assert first.plot.grid is True
    assert first.plot.camera.aspect == 1.0
    plt.draw()
    plt.draw_idle()
    plt.tight_layout()

    plt.close(second)
    assert second.number not in plt._figures
    plt.close("all")
    assert plt._figures == {}
    assert plt._current is None


def test_matplotlib_mechanisms_are_refused_explicitly():
    axes = Figure().gca()
    with pytest.raises(NotImplementedError, match="legend"):
        axes.legend()
    with pytest.raises(NotImplementedError, match="log"):
        axes.set_xscale("log")
    with pytest.raises(NotImplementedError, match="annotations"):
        axes.annotate("a", xy=(0, 0))
    with pytest.raises(NotImplementedError, match="nice numbers"):
        axes.set_xticks([0, 1])
    with pytest.raises(NotImplementedError, match="axes box"):
        Figure().add_axes((0.1, 0.1, 0.8, 0.8))


def test_figure_canvas_geometry_and_draw_gui(app: QApplication):
    figure = Figure(figsize=(4.0, 3.0), dpi=100.0)
    figure.gca().plot("sin(x)")
    canvas = FigureCanvasQt(figure)
    assert canvas.figure is figure
    assert figure.canvas is canvas
    assert canvas.get_width_height() == (400, 300)
    assert canvas.get_width_height(physical=True)[0] == pytest.approx(
        round(400 * canvas.devicePixelRatioF()), abs=1
    )
    canvas.draw()
    canvas.draw_idle()
    canvas.flush_events()
    with pytest.raises(NotImplementedError, match="mpl_connect"):
        canvas.mpl_connect("button_press_event", lambda event: None)
    with pytest.raises(NotImplementedError, match="blit"):
        canvas.blit()
    canvas.close()


def test_figure_canvas_renders_the_figure_gui(app: QApplication, tmp_path):
    figure = Figure()
    figure.gca().plot("sin(x)")
    canvas = FigureCanvasQt(figure)
    canvas.resize(480, 360)
    canvas.show()
    assert QTest.qWaitForWindowExposed(canvas), "no usable display/GPU scene graph"
    QTest.qWait(50)

    result = canvas.findChild(QQuickWidget).rootObject().grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(8000, loop.quit)
    loop.exec()
    image = result.image()
    assert image.width() > 0 and image.height() > 0
    # The default background is white, the injected curve is blue: find some of it.
    blue = sum(
        1
        for y in range(0, image.height(), 3)
        for x in range(0, image.width(), 3)
        if image.pixelColor(x, y).blue() - image.pixelColor(x, y).red() > 80
    )
    assert blue > 20, "the injected figure's curve was not rendered"

    path = tmp_path / "canvas.png"
    canvas.savefig(str(path))
    assert path.exists() and path.stat().st_size > 0
    canvas.close()


def test_axis_labels_reach_the_qml_view_gui(app: QApplication):
    figure = Figure()
    figure.gca().set_xlabel("time")
    figure.gca().set_ylabel("value")
    canvas = FigureCanvasQt(figure)
    canvas.resize(480, 360)
    canvas.show()
    assert QTest.qWaitForWindowExposed(canvas)
    QTest.qWait(50)

    root = canvas.findChild(QQuickWidget).rootObject()
    xlabel = root.findChild(QQuickItem, "xlabelText")
    ylabel = root.findChild(QQuickItem, "ylabelText")
    assert xlabel is not None and ylabel is not None
    assert xlabel.property("text") == "time"
    assert ylabel.property("text") == "value"
    assert xlabel.property("visible") is True
    assert ylabel.property("visible") is True
    canvas.close()


def test_pyplot_show_creates_and_closes_a_window_gui(app: QApplication):
    plt.figure()
    plt.plot("sin(x)")
    plt.show(block=False)
    assert len(plt._windows) == 1
    window = next(iter(plt._windows.values()))
    assert window.isVisible()
    plt.close("all")
    assert plt._windows == {}
    assert plt._figures == {}


def test_mathplotwidget_hosts_a_plot_without_its_default_curve_gui(app: QApplication):
    plot = Plot()
    widget = MathPlotWidget(expression=None, plot=plot)
    assert widget.plot is plot
    assert widget.curve is None
    assert len(widget.plot.curves) == 0
    widget.expression = "cos(x)"        # the convenience grows a curve on first assignment
    assert widget.curve is not None
    assert widget.expression == "cos(x)"
    widget.close()


def test_importing_the_package_does_not_load_qt_widgets():
    script = (
        "import sys, qmlmathplot; "
        "print('PySide6.QtWidgets' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
