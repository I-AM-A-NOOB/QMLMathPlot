"""Verification that ``MathPlotWidget`` coexists with neighbouring widgets (really opens a
window, really sends events).

The "conflicts" being ruled out:

1. In a QtWidgets layout it renders normally (QQuickWidget renders into its own FBO and does
   not occlude sibling widgets).
2. After the line edit changes the expression the plot updates; an invalid expression keeps
   the last usable curve and sets a non-empty ``error``.
3. With the plot inside a ``QScrollArea``, a wheel event over the **plot area** should zoom
   rather than scroll the parent -- the most contention-prone spot in the widgets world.
4. The plot area is ``ClickFocus`` and does not join the Tab focus chain, so the line edit
   keeps its focus.

Grabbing the image must use the asynchronous ``QQuickItem.grabToImage()``: the synchronous
``QQuickWindow.grabWindow`` deadlocks with the Python-side scene-graph objects.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from qmlmathplot import MathPlotWidget

pytestmark = pytest.mark.gui

BACKGROUND = (0x14, 0x14, 0x1E)  # background of PlotView.qml


def _quick(plot: MathPlotWidget) -> QQuickWidget:
    """The QQuickWidget inside MathPlotWidget (findChild's return type is nullable)."""
    quick = plot.findChild(QQuickWidget)
    assert quick is not None
    return quick


def _grab(plot: MathPlotWidget) -> QImage:
    """Grab the current frame of the plot widget."""
    item = _quick(plot).rootObject()
    QTest.qWait(20)                     # let the scene graph render the latest change
    result = item.grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(8000, loop.quit)
    loop.exec()
    return result.image()


def _lit_rows(image: QImage) -> int:
    """How many rows have something drawn on them (sampled every other pixel, enough to
    tell whether a curve is there)."""
    rows = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            c = image.pixelColor(x, y)
            if max(
                abs(c.red() - BACKGROUND[0]),
                abs(c.green() - BACKGROUND[1]),
                abs(c.blue() - BACKGROUND[2]),
            ) > 40:
                rows += 1
                break
    return rows


def _settle(app: QApplication, rounds: int = 25) -> None:
    for _ in range(rounds):
        app.processEvents()


def test_widget_renders_in_layout_with_siblings(app: QApplication) -> None:
    """In the same layout as a line edit and a button: the plot widget draws as usual."""
    window = QWidget()
    box = QVBoxLayout(window)
    box.addWidget(QLineEdit("sin(x)"))
    plot = MathPlotWidget("sin(x)")
    box.addWidget(plot)
    window.resize(640, 480)
    window.show()
    assert QTest.qWaitForWindowExposed(window), "no usable display/GPU scenegraph"
    _settle(app)

    assert _lit_rows(_grab(plot)) > 20, "the plot widget in the layout did not draw a curve"


def test_expression_switch_and_error_keeps_last_curve(app: QApplication) -> None:
    """Switching the expression redraws; an invalid expression keeps the last usable curve
    and reports an error."""
    plot = MathPlotWidget("sin(x)")
    plot.resize(480, 360)
    plot.show()
    assert QTest.qWaitForWindowExposed(plot)
    _settle(app)
    before = _grab(plot)

    plot.expression = "sin(1/x)"
    _settle(app)
    after = _grab(plot)
    assert after != before, "the picture did not change after switching the expression"
    assert _lit_rows(after) > 20
    assert plot.error == ""

    plot.expression = "sin("          # cannot be parsed
    _settle(app)
    assert plot.error, "the invalid expression reported no error"
    assert _lit_rows(_grab(plot)) > 20, "the invalid expression lost the previous curve"


def test_wheel_over_plot_zooms_and_does_not_scroll_parent(app: QApplication) -> None:
    """A plot widget inside a scroll area: the wheel should be swallowed by QML (zoom) and
    not leak to the parent scroll."""
    window = QWidget()
    box = QVBoxLayout(window)
    box.setContentsMargins(0, 0, 0, 0)
    area = QScrollArea()
    area.setWidgetResizable(False)
    plot = MathPlotWidget("sin(x)")
    plot.setMinimumSize(1200, 800)          # larger than the viewport -> the scroll area can really scroll
    area.setWidget(plot)
    box.addWidget(area)
    window.resize(420, 320)
    window.show()
    assert QTest.qWaitForWindowExposed(window)
    _settle(app)

    bar = area.verticalScrollBar()
    assert bar.maximum() > 0, "the scroll area cannot scroll, so this conflict cannot be tested"

    quick = _quick(plot)
    xmin, xmax, _, _ = plot.view_bounds()
    center = QPointF(quick.width() / 2, quick.height() / 2)
    wheel = QWheelEvent(
        center,
        QPointF(quick.mapToGlobal(center.toPoint())),
        QPoint(0, 0),
        QPoint(0, 120),                     # one step, positive = zoom in
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(quick, wheel)
    _settle(app)

    nxmin, nxmax, _, _ = plot.view_bounds()
    assert nxmax - nxmin < xmax - xmin, "the wheel did not reach the plot area (no zoom)"
    assert bar.value() == 0, "the wheel event leaked to the parent scroll area"


def test_plot_does_not_join_tab_focus_chain(app: QApplication) -> None:
    """The plot area does not join the Tab focus chain, so the line edit keeps its focus."""
    window = QWidget()
    box = QVBoxLayout(window)
    edit = QLineEdit()
    plot = MathPlotWidget("sin(x)")
    box.addWidget(edit)
    box.addWidget(plot)
    window.show()
    assert QTest.qWaitForWindowExposed(window)

    edit.setFocus()
    _settle(app, 10)
    assert edit.hasFocus()
    assert _quick(plot).focusPolicy() is Qt.FocusPolicy.ClickFocus


def _is_curve_pixel(color: "QColor", hue: str = "blue", delta: int = 100) -> bool:
    """Whether a pixel belongs to a curve of the given hue.

    The grid, ticks and labels are grey, and the labels are drawn with subpixel antialiasing
    (which fringes them with colour), so a plain brightness test would see them. The margin is
    wide enough to exclude that fringing but keeps every solid curve pixel.
    """
    red, blue = color.red(), color.blue()
    return blue - red > delta if hue == "blue" else red - blue > delta


def _furniture_off(plot: MathPlotWidget) -> None:
    """Turn the grid and the ticks off: they smear over the columns the tests measure."""
    plot.plot.grid = False
    plot.plot.ticks_visible = False


def _line_slope(image: QImage, hue: str = "blue") -> float:
    """Least-squares |dy/dx| of the curve's own pixels (image rows grow downwards)."""
    xs: list[float] = []
    ys: list[float] = []
    for x in range(image.width()):
        rows = [y for y in range(image.height())
                if _is_curve_pixel(image.pixelColor(x, y), hue)]
        if rows:
            xs.append(float(x))
            ys.append(sum(rows) / len(rows))
    # y = x only exists where |x| <= ymax, so it may span just a fraction of the widget
    assert len(xs) > image.width() // 5, "the line is not drawn"
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    return abs(num / den)


@pytest.mark.parametrize(("width", "height"), [(640, 400), (400, 640)])
def test_fixed_aspect_is_not_distorted_by_the_widget_shape(
    app: QApplication, width: int, height: int
) -> None:
    """With ``aspect=1`` a 45-degree line in world units is drawn at 45 degrees in pixels,
    whatever the widget's shape (that is the point of a fixed ratio)."""
    plot = MathPlotWidget("x", aspect=1.0)
    _furniture_off(plot)                            # the slope is about the curve
    plot.resize(width, height)
    plot.show()
    assert QTest.qWaitForWindowExposed(plot), "no usable display/GPU scene graph"
    _settle(app)

    assert _line_slope(_grab(plot)) == pytest.approx(1.0, abs=0.05)
    plot.hide()


def test_auto_aspect_distorts_as_before(app: QApplication) -> None:
    """The default ``"auto"`` aspect keeps the old behaviour: the two scales stay independent
    (the widget's shape decides), so the same line is not at 45 degrees."""
    plot = MathPlotWidget("x")                      # aspect defaults to "auto"
    _furniture_off(plot)
    plot.resize(640, 400)
    plot.show()
    assert QTest.qWaitForWindowExposed(plot)
    _settle(app)

    slope = _line_slope(_grab(plot))
    assert slope > 1.5, f"expected the stretched slope, got {slope:.2f}"
    plot.hide()


def test_resize_keeps_the_scale_with_a_fixed_aspect(app: QApplication) -> None:
    """With a fixed aspect, resizing must not zoom: the world units per pixel stay put and
    the visible range follows the widget (the shape and the apparent size are unaffected)."""
    plot = MathPlotWidget("x", aspect=1.0)
    plot.resize(900, 600)
    plot.show()
    assert QTest.qWaitForWindowExposed(plot)
    _settle(app)

    def scale() -> tuple[float, float]:
        xmin, xmax, ymin, ymax = plot.view_bounds()
        return ((xmax - xmin) / plot.width(), (ymax - ymin) / plot.height())

    start = scale()
    for width, height in ((600, 600), (1200, 400), (500, 900)):
        plot.resize(width, height)
        _settle(app)
        now = scale()
        assert now[0] == pytest.approx(start[0], rel=0.02), f"{width}x{height} changed the x scale"
        assert now[1] == pytest.approx(start[1], rel=0.02), f"{width}x{height} changed the y scale"

    plot.resize(900, 600)
    _settle(app)
    assert scale() == pytest.approx(start, rel=0.02)     # and it is reversible
    plot.hide()


def _rows_of_colour(image: QImage, hue: str) -> int:
    """Rows holding a pixel of one curve hue: only the curves count, never the furniture."""
    rows = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            if _is_curve_pixel(image.pixelColor(x, y), hue):
                rows += 1
                break
    return rows


def test_curves_can_be_added_hidden_and_removed_through_the_model(app: QApplication) -> None:
    """The widget's conveniences cover one curve; the model behind it carries the rest: a
    second curve is another shader pass, and ``visible`` takes it away again."""
    plot = MathPlotWidget("sin(x)")                 # the default cycle starts with blue
    _furniture_off(plot)                            # only the curves should count
    plot.resize(480, 360)
    plot.show()
    assert QTest.qWaitForWindowExposed(plot)
    _settle(app)
    assert _rows_of_colour(_grab(plot), "blue") > 20, "the widget's own curve is not drawn"

    extra = plot.plot.add_curve("x", color="#ff8866", line_width=3.0)
    _settle(app)
    assert _rows_of_colour(_grab(plot), "red") > 20, "the added curve is not drawn"

    extra.visible = False
    _settle(app)
    assert _rows_of_colour(_grab(plot), "red") == 0, "the hidden curve is still drawn"
    assert _rows_of_colour(_grab(plot), "blue") > 20, "hiding one curve hid the other"

    plot.plot.curves.remove_curve(extra)
    _settle(app)
    assert len(plot.plot.curves) == 1
    assert _rows_of_colour(_grab(plot), "red") == 0, "the removed curve is still drawn"
    plot.hide()
