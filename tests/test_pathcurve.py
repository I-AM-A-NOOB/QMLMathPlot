"""Prototype path renderer: one QPainterPath per curve, like GeoGebra's IntervalPathPlotter."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQml import qmlRegisterType
from PySide6.QtQuick import QQuickView

from qmlmathplot.pathcurve import PathCurveItem

pytestmark = pytest.mark.gui

WIDTH, HEIGHT = 900, 600


def _render(app: QGuiApplication, tmp_path, expression: str,
            xlim: tuple[float, float], ylim: tuple[float, float]) -> QImage:
    qml = f"""
import QtQuick
import Test 1.0
Item {{
    width: {WIDTH}; height: {HEIGHT}
    Path {{
        objectName: "path"
        anchors.fill: parent
        expression: {expression!r}
        camera: Qt.vector4d({(xlim[0] + xlim[1]) / 2}, {(ylim[0] + ylim[1]) / 2},
                            {(xlim[1] - xlim[0]) / WIDTH}, {(ylim[1] - ylim[0]) / HEIGHT})
    }}
}}
"""
    qml_file = tmp_path / "path.qml"
    qml_file.write_text(qml, encoding="utf-8")
    view = QQuickView()
    view.setSource(QUrl.fromLocalFile(str(qml_file)))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    view.show()
    for _ in range(200):
        app.processEvents()
        if view.isExposed():
            break
    result = view.rootObject().grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    view.close()
    return result.image()


def _lit(image: QImage, x: int, y: int) -> bool:
    c = image.pixelColor(x, y)
    return c.alpha() > 10 and (c.red() < 220 or c.green() < 220 or c.blue() < 220)


def test_tan_path_does_not_bridge_a_pole(app: QGuiApplication, tmp_path) -> None:
    qmlRegisterType(PathCurveItem, "Test", 1, 0, "Path")
    image = _render(app, tmp_path, "tan(x)", (-6.0, 6.0), (-2.0, 2.0))
    height = image.height()
    # The pole at x = -pi/2 maps to the centre column ± a few pixels; a connector would
    # light nearly the whole column, while the branches leave through the top/bottom.
    pole_x = int(image.width() * ((-1.5707963 + 6.0) / 12.0))
    lit = sum(_lit(image, x, y) for x in range(pole_x - 1, pole_x + 2)
              for y in range(height))
    assert lit < height * 0.4, f"tan's pole column is bridged: {lit} of {3 * height}"


def test_log_path_stays_inside_its_domain(app: QGuiApplication, tmp_path) -> None:
    qmlRegisterType(PathCurveItem, "Test", 1, 0, "Path")
    image = _render(app, tmp_path, "log(x)", (-2.0, 2.0), (-6.0, 6.0))
    width = image.width()
    # No path pixel to the left of the y axis (x < 0): sample the left quarter.
    lit = sum(_lit(image, x, y) for x in range(0, width // 2 - 10)
              for y in range(image.height()))
    assert lit == 0, f"log(x) painted outside its domain: {lit} pixels"
