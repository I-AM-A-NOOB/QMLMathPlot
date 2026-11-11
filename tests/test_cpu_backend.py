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
    root.setProperty("renderMode", "cpu")
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


def test_implicit_curves_say_they_need_the_gpu(app: QGuiApplication) -> None:
    """CPU mode cannot draw a level set: say so visibly, and draw nothing for it."""
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(WIDTH, HEIGHT)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    root = view.rootObject()
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.add_curve("x**2 + y**2 - 1")
    root.setProperty("plot", plot)
    root.setProperty("renderMode", "cpu")
    view.show()
    for _ in range(200):
        app.processEvents()
        if view.isExposed():
            break
    else:
        pytest.skip("no usable display/GPU scenegraph")
    plot.camera.set_viewport(float(WIDTH), float(HEIGHT))
    plot.camera.xlim = (-2.0, 2.0)
    plot.camera.ylim = (-2.0, 2.0)
    for _ in range(30):
        app.processEvents()
    result = root.grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    image: QImage = result.image()
    warning = sum(
        1
        for y in range(0, image.height(), 2)
        for x in range(0, image.width(), 2)
        if image.pixelColor(x, y).red() > 150
        and image.pixelColor(x, y).green() < 100
        and image.pixelColor(x, y).blue() < 100
    )
    curve = sum(
        1
        for y in range(0, image.height(), 2)
        for x in range(0, image.width(), 2)
        if image.pixelColor(x, y).blue() > 100 and image.pixelColor(x, y).red() < 120
    )
    view.close()
    assert warning > 0, "the implicit curve did not show the GPU-renderer warning"
    assert curve == 0, "CPU mode must not draw an implicit curve"
