"""CPU fallback: QPainterPath rendering when RHI is not available."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickView

from qmlmathplot import Plot, qml_component_path

pytestmark = pytest.mark.gui

WIDTH, HEIGHT = 900, 600


def test_cpu_renderer_draws_a_curve(app: QGuiApplication) -> None:
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(WIDTH, HEIGHT)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    root = view.rootObject()
    plot = Plot()
    plot.add_curve("tan(x)")
    root.setProperty("plot", plot)
    root.setProperty("cpuRenderer", True)
    view.show()
    for _ in range(200):
        app.processEvents()
        if view.isExposed():
            break
    else:
        pytest.skip("no usable display/GPU scenegraph")
    camera = plot.camera
    camera.set_viewport(float(WIDTH), float(HEIGHT))
    camera.xlim = (-6.0, 6.0)
    camera.ylim = (-2.0, 2.0)
    for _ in range(20):
        app.processEvents()
    result = root.grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    image: QImage = result.image()
    assert image.width() > 0
    lit = sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).blue() > 100 and image.pixelColor(x, y).red() < 120
    )
    assert lit > 1000, "the CPU QPainterPath backend drew no curve"
    view.close()
