"""``MathPlotWidget`` 与邻居控件的共存验证（真开窗、真发事件）。

要排除的"打架"点：

1. 放进 QtWidgets 布局能正常渲染（QQuickWidget 渲染到自己的 FBO，不遮挡兄弟控件）。
2. 输入框换表达式后图形更新；非法表达式保留上一份可用图形，``error`` 非空。
3. 绘图控件放进 ``QScrollArea``：滚轮落在**绘图区**应当缩放，而不是滚动父级
   —— widgets 世界里最容易打架的一处。
4. 绘图区是 ``ClickFocus``，不进 Tab 焦点链，输入框的焦点不会被抢。

取图必须用异步的 ``QQuickItem.grabToImage()``：同步取图（``QQuickWindow.grabWindow``）
会和 Python 侧的场景图对象死锁。
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

BACKGROUND = (0x14, 0x14, 0x1E)  # MathPlot.qml 的 backgroundColor


def _quick(plot: MathPlotWidget) -> QQuickWidget:
    """MathPlotWidget 内部那个 QQuickWidget（findChild 的返回类型是可空的）。"""
    quick = plot.findChild(QQuickWidget)
    assert quick is not None
    return quick


def _grab(plot: MathPlotWidget) -> QImage:
    """取绘图控件的当前帧。"""
    item = _quick(plot).rootObject()
    result = item.grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(8000, loop.quit)
    loop.exec()
    return result.image()


def _lit_rows(image: QImage) -> int:
    """有多少行画上了东西（隔点采样，够判断"有没有曲线"）。"""
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
    """和输入框、按钮同处一个布局：绘图控件照常出图。"""
    window = QWidget()
    box = QVBoxLayout(window)
    box.addWidget(QLineEdit("sin(x)"))
    plot = MathPlotWidget("sin(x)")
    box.addWidget(plot)
    window.resize(640, 480)
    window.show()
    assert QTest.qWaitForWindowExposed(window), "没有可用的显示/GPU 场景图"
    _settle(app)

    assert _lit_rows(_grab(plot)) > 20, "布局里的绘图控件没有画出曲线"


def test_expression_switch_and_error_keeps_last_curve(app: QApplication) -> None:
    """换表达式出图；非法表达式保留上一份可用图形并给出 error。"""
    plot = MathPlotWidget("sin(x)")
    plot.resize(480, 360)
    plot.show()
    assert QTest.qWaitForWindowExposed(plot)
    _settle(app)
    before = _grab(plot)

    plot.expression = "sin(1/x)"
    _settle(app)
    after = _grab(plot)
    assert after != before, "换表达式后画面没变"
    assert _lit_rows(after) > 20
    assert plot.error == ""

    plot.expression = "sin("          # 解析不了
    _settle(app)
    assert plot.error, "非法表达式没有报错"
    assert _lit_rows(_grab(plot)) > 20, "非法表达式把上一份图形弄丢了"


def test_wheel_over_plot_zooms_and_does_not_scroll_parent(app: QApplication) -> None:
    """滚动区里的绘图控件：滚轮应当被 QML 吃掉（缩放），不漏给父级滚动。"""
    window = QWidget()
    box = QVBoxLayout(window)
    box.setContentsMargins(0, 0, 0, 0)
    area = QScrollArea()
    area.setWidgetResizable(False)
    plot = MathPlotWidget("sin(x)")
    plot.setMinimumSize(1200, 800)          # 比视口大 -> 滚动区真的能滚
    area.setWidget(plot)
    box.addWidget(area)
    window.resize(420, 320)
    window.show()
    assert QTest.qWaitForWindowExposed(window)
    _settle(app)

    bar = area.verticalScrollBar()
    assert bar.maximum() > 0, "滚动区不可滚，这个冲突测不出来"

    quick = _quick(plot)
    xmin, xmax, _, _ = plot.view_bounds()
    center = QPointF(quick.width() / 2, quick.height() / 2)
    wheel = QWheelEvent(
        center,
        QPointF(quick.mapToGlobal(center.toPoint())),
        QPoint(0, 0),
        QPoint(0, 120),                     # 一档，正 = 放大
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(quick, wheel)
    _settle(app)

    nxmin, nxmax, _, _ = plot.view_bounds()
    assert nxmax - nxmin < xmax - xmin, "滚轮没有落到绘图区（没有缩放）"
    assert bar.value() == 0, "滚轮漏给了父级滚动区"


def test_plot_does_not_join_tab_focus_chain(app: QApplication) -> None:
    """绘图区不进 Tab 焦点链，输入框的焦点不会被抢。"""
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
