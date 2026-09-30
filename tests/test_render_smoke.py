"""端到端烟测：真的开窗渲染，取像素验证两块拼图（屏幕空间描边 + 欠采样包络带）。

取图必须用异步的 ``QQuickItem.grabToImage()``：``QQuickWindow.grabWindow()`` 的
同步握手会和 Python 侧的场景图对象死锁（表现为窗口"未响应"）。

后端由环境变量 ``QSG_RHI_BACKEND`` 决定（不设 = Qt 默认）。
"""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from qmlmathplot import PlotController, qml_component_path, register_qml_types

pytestmark = pytest.mark.gui

WIDTH, HEIGHT = 900, 600
BACKGROUND = (0x14, 0x14, 0x1E)  # MathPlot.qml 的 backgroundColor


@pytest.fixture(scope="module")
def app() -> QGuiApplication:
    application = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    register_qml_types()
    return application


def _render(app: QGuiApplication, expression: str | None = None) -> QImage:
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(WIDTH, HEIGHT)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]

    root = view.rootObject()
    if expression is not None:
        # MVVM：App 侧创建 ViewModel 并注入组件（组件也会自带一个，这里走注入路径）
        root.setProperty("controller", PlotController(expression))
    view.show()
    if not QTest.qWaitForWindowExposed(view):
        pytest.skip("没有可用的显示/GPU 场景图")

    for _ in range(20):
        app.processEvents()
    result = root.grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    image = result.image()
    view.hide()
    return image


def _lit(image: QImage, x: int, y: int, threshold: int = 20) -> bool:
    color = image.pixelColor(x, y)
    return (abs(color.red() - BACKGROUND[0]) + abs(color.green() - BACKGROUND[1])
            + abs(color.blue() - BACKGROUND[2])) > threshold


def _lit_count(image: QImage) -> int:
    return sum(_lit(image, x, y) for y in range(image.height()) for x in range(image.width()))


def _column_ratio(image: QImage, x: int) -> float:
    """某一列的点亮比例（与 devicePixelRatio 无关）。"""
    return sum(_lit(image, x, y) for y in range(image.height())) / image.height()


def _center_column_ratio(image: QImage) -> float:
    """图像中心列（世界 x≈0，即 sin(1/x) 的奇点）的点亮比例。"""
    return _column_ratio(image, image.width() // 2)


def test_smooth_curve_is_a_thin_stroke(app: QGuiApplication) -> None:
    image = _render(app, "sin(x)")
    width, height = image.width(), image.height()
    assert _lit_count(image) > 100, "曲线上什么都没有"
    assert _lit(image, width // 2, height // 2), "sin(0)=0 应过视图中心"
    assert not _lit(image, width // 2, height // 10), "曲线上方应是背景"
    ratio = _center_column_ratio(image)
    assert ratio < 0.05, f"中心列应只有一条细线，实际点亮 {ratio:.3f}"


def test_component_works_without_injection(app: QGuiApplication) -> None:
    """不注入 ViewModel 时组件自带一个（README 承诺的独立可用）。"""
    image = _render(app)
    assert _lit_count(image) > 100, "自带 controller 的默认表达式 sin(x) 应画出曲线"


def _solid_outside_pm1(image: QImage) -> int:
    """中心 41 列里，落在 |y|>1 之外的**满覆盖**像素数（阈值取满色，不算羽化/收细）。

    sin(1/x) 的值域是 ±1，超出即伪影：曾经因为"宏参数没加括号"导致采样点全算错、
    切线外推把整列涂满，实测溢出 7961 个像素。
    """
    height = image.height()
    columns = range(image.width() // 2 - 20, image.width() // 2 + 21)
    rows = list(range(0, int(height * 0.25))) + list(range(int(height * 0.75), height))
    return sum(_lit(image, x, y, threshold=200) for x in columns for y in rows)


def _solid_band_columns(image: QImage) -> int:
    """实心带（每列点亮 >40% 高度）的列数，用来盯住"别为了消锯齿把带撑太宽"。"""
    height = image.height()
    return sum(1 for x in range(image.width())
               if sum(_lit(image, x, y, threshold=60) for y in range(height)) > 0.4 * height)


def test_undersampled_column_fills_envelope_within_pm1(app: QGuiApplication) -> None:
    """sin(1/x) 奇点列填真实 ±1 包络（约半列），不得画到 ±1 之外，宽度也不能失控。

    带区宽度实测 8 逻辑列（贴近理论不可分辨区 ~7 列）：判据只看本列，包络取 ±8 列的
    32 个采样点（够密才不会被相位噪声咬出缺齿），描边端头 4px 收细而不是方切。
    """
    image = _render(app, "sin(1/x)")
    ratio = _center_column_ratio(image)
    assert 0.3 < ratio < 0.7, f"奇点列应填 ±1 包络（约半列），实际 {ratio:.2f}"
    outside = _solid_outside_pm1(image)
    # 带边缘 2px 羽化 + 描边端头 4px 收细会在 ±1 外留 1~2 行部分覆盖（约 2 像素/列），
    # 这里只挡真正的溢出（宏参数缺括号那次是 7961）。
    assert outside < 200, f"sin(1/x) 值域是 ±1，不该有成片的满覆盖像素在之外，实际 {outside}"
    width = _solid_band_columns(image) / 1.5  # 抓图带 devicePixelRatio
    assert 5 < width < 60, f"实心带宽度应在个位数~几十逻辑列，实际 {width:.0f}"


def test_smooth_column_stays_thin(app: QGuiApplication) -> None:
    ratio = _center_column_ratio(_render(app, "sin(x)"))
    assert ratio < 0.05, f"sin(x) 在 x=0 处应仍是细线，实际 {ratio:.2f}"
