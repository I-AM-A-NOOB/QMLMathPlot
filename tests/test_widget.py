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

BACKGROUND = (0x14, 0x14, 0x1E)  # backgroundColor of MathPlot.qml


def _quick(plot: MathPlotWidget) -> QQuickWidget:
    """The QQuickWidget inside MathPlotWidget (findChild's return type is nullable)."""
    quick = plot.findChild(QQuickWidget)
    assert quick is not None
    return quick


def _grab(plot: MathPlotWidget) -> QImage:
    """Grab the current frame of the plot widget."""
    item = _quick(plot).rootObject()
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
