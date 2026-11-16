"""Furniture layers must not allocate Canvas textures unless their layer is used."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtQuick import QQuickView

from qmlmathplot import Plot, qml_component_path

pytestmark = pytest.mark.gui


def _live_canvases(root) -> int:
    count = 0
    stack = [root]
    while stack:
        item = stack.pop()
        if "Canvas" in item.metaObject().className():
            count += 1
        stack.extend(item.children())
    return count


def _settle(app) -> None:
    loop = QEventLoop()
    QTimer.singleShot(30, loop.quit)
    loop.exec()
    app.processEvents()


def test_unused_furniture_layers_do_not_create_canvases(app) -> None:
    plot = Plot()  # grid and minor ticks are off; no guides
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(900, 700)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    root = view.rootObject()
    root.setProperty("plot", plot)
    view.show()
    for _ in range(200):
        app.processEvents()
        if view.isExposed():
            break
    else:
        pytest.skip("no usable display/GPU scenegraph")

    try:
        _settle(app)
        assert _live_canvases(root) == 0

        plot.grid = True
        _settle(app)
        assert _live_canvases(root) == 1

        plot.minor_ticks = True
        _settle(app)
        assert _live_canvases(root) == 2

        guide = plot.add_hline(0.0)
        _settle(app)
        assert _live_canvases(root) == 3

        plot.remove_guide(guide)
        _settle(app)
        assert _live_canvases(root) == 2

        plot.grid = False
        plot.minor_ticks = False
        _settle(app)
        assert _live_canvases(root) == 0
    finally:
        view.setSource(QUrl())
        app.processEvents()
        view.close()
