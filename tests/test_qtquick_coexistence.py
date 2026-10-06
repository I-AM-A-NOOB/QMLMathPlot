"""The plot inside a QML ``Flickable``: dragging must pan the plot, not scroll the parent.

This is the Qt Quick counterpart of the ``QScrollArea`` case in ``test_widget.py``. Without
``preventStealing: true`` on the plot's MouseArea, a Flickable steals the mouse grab as soon
as the drag passes its threshold, and the plot only pans a handful of pixels before it
stops.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtQuick import QQuickItem, QQuickView
from PySide6.QtTest import QTest

pytestmark = pytest.mark.gui

WIDTH, HEIGHT = 480, 360
PLOT_WIDTH, PLOT_HEIGHT = 1200, 800

QML = f"""
import QtQuick
import QmlMathPlot 1.0

Item {{
    width: {WIDTH}
    height: {HEIGHT}

    Flickable {{
        id: flick
        objectName: "flick"
        anchors.fill: parent
        contentWidth: {PLOT_WIDTH}
        contentHeight: {PLOT_HEIGHT}

        MathPlot {{
            objectName: "plot"
            width: {PLOT_WIDTH}
            height: {PLOT_HEIGHT}
            controller.expression: "sin(x)"
        }}
    }}
}}
"""


def _send(window: QQuickView, kind: QEvent.Type, pos: QPointF,
          button: Qt.MouseButton, buttons: Qt.MouseButton) -> None:
    QGuiApplication.sendEvent(
        window,
        QMouseEvent(kind, pos, QPointF(window.mapToGlobal(pos.toPoint())), button, buttons,
                    Qt.KeyboardModifier.NoModifier),
    )


def _drag(window: QQuickView, start: QPointF, delta: QPointF, steps: int = 12) -> None:
    _send(window, QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton,
          Qt.MouseButton.LeftButton)
    for i in range(1, steps + 1):
        _send(window, QEvent.Type.MouseMove, start + delta * (i / steps),
              Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    _send(window, QEvent.Type.MouseButtonRelease, start + delta, Qt.MouseButton.LeftButton,
          Qt.MouseButton.NoButton)


def test_drag_over_plot_pans_instead_of_flicking(app: QGuiApplication, tmp_path: Path) -> None:
    qml_file = tmp_path / "flickable_plot.qml"
    qml_file.write_text(QML, encoding="utf-8")

    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(WIDTH, HEIGHT)
    view.setSource(QUrl.fromLocalFile(str(qml_file)))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    view.show()
    assert QTest.qWaitForWindowExposed(view), "no usable display/GPU scene graph"
    for _ in range(20):
        app.processEvents()

    plot = view.rootObject().findChild(QQuickItem, "plot")
    flick = view.rootObject().findChild(QQuickItem, "flick")
    assert plot is not None and flick is not None

    xmin_before = plot.property("view").x()
    span = plot.property("view").y() - xmin_before

    drag_pixels = -300.0
    _drag(view, QPointF(WIDTH / 2, HEIGHT / 2), QPointF(drag_pixels, 0.0))
    for _ in range(20):
        app.processEvents()

    xmin_after = plot.property("view").x()
    # Panning is 1:1 with the cursor in plot coordinates: -300 px of a 1200 px wide plot.
    expected = -drag_pixels / PLOT_WIDTH * span
    moved = xmin_after - xmin_before
    assert abs(moved - expected) < 0.05 * abs(expected), (
        f"plot panned {moved:.3f} world units, expected ≈ {expected:.3f} "
        "(a Flickable likely stole the drag)"
    )
    assert flick.property("contentX") == 0, "the drag leaked to the Flickable"
