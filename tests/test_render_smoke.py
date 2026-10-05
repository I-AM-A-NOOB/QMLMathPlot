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


def _render(app: QGuiApplication, expression: str | None = None,
            pan_pixels: float = 0.0) -> QImage:
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(WIDTH, HEIGHT)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]

    root = view.rootObject()
    controller = PlotController(expression) if expression is not None else None
    if controller is not None:
        # MVVM：App 侧创建 ViewModel 并注入组件（组件也会自带一个，这里走注入路径）
        root.setProperty("controller", controller)
    view.show()
    if not QTest.qWaitForWindowExposed(view):
        pytest.skip("没有可用的显示/GPU 场景图")
    if pan_pixels and controller is not None:
        controller.panPixels(0.0, pan_pixels, float(WIDTH), float(HEIGHT))

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
    """中心 41 列里，落在 |y|>1 之外的**实心**像素数（阈值取高，不算抗锯齿羽化）。

    sin(1/x) 的值域是 ±1，超出即伪影：曾经因为"宏参数没加括号"导致采样点全算错、
    切线外推把整列涂满，实测溢出 7961 个像素。
    """
    height = image.height()
    columns = range(image.width() // 2 - 20, image.width() // 2 + 21)
    rows = list(range(0, int(height * 0.25))) + list(range(int(height * 0.75), height))
    return sum(_lit(image, x, y, threshold=150) for x in columns for y in rows)


def _solid_band_columns(image: QImage) -> int:
    """实心带（每列点亮 >40% 高度）的列数，用来盯住"别为了消锯齿把带撑太宽"。"""
    height = image.height()
    return sum(1 for x in range(image.width())
               if sum(_lit(image, x, y, threshold=60) for y in range(height)) > 0.4 * height)


def test_undersampled_column_fills_envelope_within_pm1(app: QGuiApplication) -> None:
    """sin(1/x) 奇点列填真实 ±1 包络（约半列），不得画到 ±1 之外，宽度也不能失控。

    带区宽度实测 20 逻辑列（numpy 参考版同样是 20/900）——采样窗口放宽是为了消掉
    梳状锯齿（相邻列上边缘差 93px → 2px），代价是填充范围略宽，是有意取舍。
    """
    image = _render(app, "sin(1/x)")
    ratio = _center_column_ratio(image)
    assert 0.3 < ratio < 0.7, f"奇点列应填 ±1 包络（约半列），实际 {ratio:.2f}"
    outside = _solid_outside_pm1(image)
    assert outside < 50, f"sin(1/x) 值域是 ±1，不该有实心像素在之外，实际 {outside}"
    width = _solid_band_columns(image) / 1.5  # 抓图带 devicePixelRatio
    assert 5 < width < 60, f"实心带宽度应在个位数~几十逻辑列，实际 {width:.0f}"


def test_smooth_column_stays_thin(app: QGuiApplication) -> None:
    ratio = _center_column_ratio(_render(app, "sin(x)"))
    assert ratio < 0.05, f"sin(x) 在 x=0 处应仍是细线，实际 {ratio:.2f}"


def _column_lit(image: QImage, world_x: float) -> int:
    """给定世界 x 处那一列的点亮像素数。"""
    x = min(image.width() - 1, max(0, int((world_x + 6) / 12 * image.width())))
    return sum(_lit(image, x, y) for y in range(image.height()))


def test_asymptote_is_not_connected(app: QGuiApplication) -> None:
    """1/x、tan(x) 在极点处不得画出竖直连线（值域 ±∞ 的跳变）。"""
    for expr, poles in (("1/x", [0.0]), ("tan(x)", [1.5708])):
        image = _render(app, expr)
        for pole in poles:
            lit = _column_lit(image, pole)
            assert lit < 30, f"{expr} 在 x={pole} 处不应有连线，实际点亮 {lit} 像素"


def test_out_of_domain_is_blank(app: QGuiApplication) -> None:
    """log(x)、sqrt(x)、asin(x) 在定义域外不画（x<0 / |x|>1）。"""
    for expr in ("log(x)", "sqrt(x)"):
        image = _render(app, expr)
        left = sum(_lit(image, x, y) for x in range(0, image.width() // 2 - 4)
                   for y in range(0, image.height(), 5))
        assert left == 0, f"{expr} 在 x<0 不该有像素，实际 {left}"
    image = _render(app, "asin(x)")
    # 左端 x ∈ [-6, -1]：|x|>1 全部落在定义域外
    far = sum(_lit(image, x, y) for x in range(0, int((-1.0 + 6) / 12 * image.width()))
              for y in range(0, image.height(), 5))
    assert far == 0, f"asin(x) 在 |x|>1 不该有像素，实际 {far}"


def test_log_descent_is_not_cut(app: QGuiApplication) -> None:
    """log(x) 在 x→0+ 要一路画到视口外，不能被采样包络下界切断（"逐渐变细消失"）。

    视口下移 6 个单位（y∈[-8,-4]）：x≈0.004 那一列的曲线在本列内就从 -4.5 扫到 -∞，
    必须有点亮像素落到视口底边附近。
    """
    image = _render(app, "log(x)", pan_pixels=-900.0)
    height = image.height()
    column = _column_lit(image, 0.004)
    assert column > 0, "log(x) 在 x≈0.004 处应有像素"
    # 下降段就在中心列附近（x≈0.004 处 log 已到 -7 以下，整列都该有像素）
    center = image.width() // 2
    bottom = sum(_lit(image, x, y) for x in range(center - 4, center + 5)
                 for y in range(int(height * 0.97), height))
    assert bottom > 0, "log(x) 的下降段应一直画到视口底边"


def test_log_descends_into_deep_views(app: QGuiApplication) -> None:
    """log(x) 的下降段在深视口里也必须可见（x→0+ 慢发散，采样窗口够不到）。

    视口下移到 y∈[-16,-12] 时，可见的曲线全落在 x∈[1e-7,6e-6]，即紧贴定义域边界
    x=0；固定宽度的采样窗口最左只能采到 log≈-7，整段会消失。修复后由"无界边界
    射线"沿 x=0 画出，表现为贴着 y 轴的一整列竖直下降线。
    """
    image = _render(app, "log(x)", pan_pixels=-2100.0)
    height = image.height()
    center = image.width() // 2
    col = sum(_lit(image, x, y) for x in range(center - 3, center + 4)
              for y in range(height))
    assert col > 0.5 * height, f"深视口里 log(x) 应贴着 x=0 有一整列下降线，实际 {col} 像素"
