"""The single-pass QRhi renderer: every curve is drawn by one QQuickRhiItem."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickView

from qmlmathplot import Plot, qml_component_path
from qmlmathplot.rhi import CurveRhiItem

pytestmark = pytest.mark.gui

WIDTH, HEIGHT = 900, 600
BLUE = (31, 119, 180)
RED = (214, 39, 40)


def _wait_exposed(app: QGuiApplication, view: QQuickView) -> None:
    for _ in range(200):
        app.processEvents()
        if view.isExposed():
            return
    pytest.skip("no usable display/GPU scenegraph")


def _grab(app: QGuiApplication, root) -> QImage:
    for _ in range(30):
        app.processEvents()
    result = root.grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    return result.image()


def _render(app: QGuiApplication, plot: Plot) -> tuple[QQuickView, object, CurveRhiItem, QImage]:
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(WIDTH, HEIGHT)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    root = view.rootObject()
    root.setProperty("plot", plot)
    view.show()
    _wait_exposed(app, view)
    camera = plot.camera
    camera.set_viewport(float(WIDTH), float(HEIGHT))
    camera.xlim = (-6.0, 6.0)
    camera.ylim = (-2.0, 2.0)
    item = root.findChild(CurveRhiItem)
    assert item is not None
    return view, root, item, _grab(app, root)


def _count(image: QImage, rgb: tuple[int, int, int], tolerance: int = 40) -> int:
    r, g, b = rgb
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if (lambda c: abs(c.red() - r) + abs(c.green() - g) + abs(c.blue() - b) < tolerance)(
            image.pixelColor(x, y)
        )
    )


def test_all_curves_are_drawn_by_one_rhi_item(app: QGuiApplication) -> None:
    plot = Plot()
    plot.add_curve("sin(x)", color="#1f77b4")
    plot.add_curve("cos(x)", color="#d62728")
    view, root, item, image = _render(app, plot)
    try:
        assert _count(image, BLUE) > 1000
        assert _count(image, RED) > 1000
        assert item.curves is plot.curves
    finally:
        view.close()


def test_visibility_and_style_changes_reach_the_renderer(app: QGuiApplication) -> None:
    plot = Plot()
    first = plot.add_curve("sin(x)", color="#1f77b4")
    second = plot.add_curve("cos(x)", color="#d62728")
    view, root, item, image = _render(app, plot)
    try:
        assert _count(image, RED) > 1000

        second.visible = False
        image = _grab(app, root)
        assert _count(image, RED) < 100

        second.visible = True
        first.color = "#d62728"
        image = _grab(app, root)
        assert _count(image, RED) > 1000
    finally:
        view.close()


def test_a_curve_added_after_exposure_is_drawn(app: QGuiApplication) -> None:
    plot = Plot()
    view, root, item, image = _render(app, plot)
    try:
        assert _count(image, BLUE) < 100
        plot.add_curve("sin(x)", color="#1f77b4")
        image = _grab(app, root)
        assert _count(image, BLUE) > 1000
    finally:
        view.close()



def test_zooming_does_not_exhaust_the_update_batch_pool() -> None:
    """A subprocess: a Qt message handler on the scene-graph thread can deadlock pytest."""
    script = r"""
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QUrl, qInstallMessageHandler
from PySide6.QtQuick import QQuickView
from qmlmathplot import Plot, qml_component_path, register_qml_types

bad = []
qInstallMessageHandler(lambda mode, ctx, msg: bad.append(msg)
                       if "Resource update batch pool exhausted" in msg else None)
app = QApplication(sys.argv[:1])
register_qml_types()
view = QQuickView()
view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
view.resize(900, 600)
view.setSource(QUrl.fromLocalFile(qml_component_path()))
root = view.rootObject()
plot = Plot()
plot.add_curve("sin(x)")
root.setProperty("plot", plot)
plot.camera.set_viewport(900.0, 600.0)
plot.camera.xlim = (-6.0, 6.0)
plot.camera.ylim = (-2.0, 2.0)
view.show()
for _ in range(200):
    app.processEvents()
    if view.isExposed():
        break
for i in range(150):
    plot.camera.zoom_by(120.0 if i % 2 else -120.0, 0.5, 0.5, 900.0, 600.0)
    app.processEvents()
print("EXHAUSTED" if bad else "OK")
view.close()
"""
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout, result.stdout + result.stderr
