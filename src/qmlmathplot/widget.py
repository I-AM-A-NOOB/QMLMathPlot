"""把绘图组件包成普通 QWidget，直接塞进任何 QtWidgets 布局。

    from qmlmathplot import MathPlotWidget

    plot = MathPlotWidget("sin(1/x)")
    layout.addWidget(plot)
    plot.expression = "tan(x)"       # 改表达式
    plot.reset_view()

QML 组件（View）本身是 Qt Quick 的 Item，这里用 ``QQuickWidget`` 把它桥进 widgets
世界：它渲染到自己的 FBO，所以能和兄弟控件（输入框、滚动区、splitter、覆盖层）
正常叠放与共处；滚轮与左键拖拽在 QML 侧已 ``accepted``，不会漏给父级滚动区。

需要先有 ``QApplication``（QQuickWidget 属于 QtWidgets）；``register_qml_types()``
在这里自动调用，可重复调用。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from .viewmodel import PlotController, qml_component_path, register_qml_types

__all__ = ["MathPlotWidget"]


class MathPlotWidget(QWidget):
    """函数绘图控件（QWidget 版）。

    :param expression: sympy 语法表达式，默认 ``"sin(x)"``
    :param line_width: 线宽（逻辑像素）
    :param curve_color: 曲线颜色（``#rrggbb``）
    :param background_color: 背景色（``#rrggbb``）

    信号 ``expressionChanged`` / ``errorChanged`` 与 ViewModel 同步；
    ``error`` 非空时表示表达式解析失败（此时保留上一份可用图形）。
    """

    expressionChanged = Signal()
    errorChanged = Signal()

    def __init__(
        self,
        expression: str = "sin(x)",
        parent: QWidget | None = None,
        *,
        line_width: float = 1.5,
        curve_color: str = "#33ccff",
        background_color: str = "#14141e",
    ) -> None:
        super().__init__(parent)
        if QApplication.instance() is None:
            raise RuntimeError("请先创建 QApplication —— QQuickWidget 需要 QtWidgets")
        register_qml_types()

        self._quick = QQuickWidget(self)
        self._quick.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        # 点一下绘图区不该进 Tab 焦点链（滚轮/拖拽仍由 QML 正常处理）
        self._quick.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._quick.setSource(QUrl.fromLocalFile(qml_component_path()))
        if self._quick.status() is not QQuickWidget.Status.Ready:
            raise RuntimeError(
                "QML 组件加载失败：" + "; ".join(e.toString() for e in self._quick.errors())
            )

        self._root = self._quick.rootObject()   # status 已 Ready，这里必定有效
        self._controller = PlotController(expression, self)
        self._root.setProperty("controller", self._controller)
        self._controller.expressionChanged.connect(self.expressionChanged)
        self._controller.errorChanged.connect(self.errorChanged)

        self._root_set("lineWidth", float(line_width))
        self._root_set("curveColor", curve_color)
        self._root_set("backgroundColor", background_color)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self._quick)

    # ------------------------------------------------------------- ViewModel
    @property
    def controller(self) -> PlotController:
        """底层 ViewModel（要接更多信号时用）。"""
        return self._controller

    @property
    def expression(self) -> str:
        return self._controller.expression

    @expression.setter
    def expression(self, source: str) -> None:
        self._controller.expression = source

    @property
    def error(self) -> str:
        """最近一次解析错误（空字符串 = 正常）。"""
        return self._controller.error

    # ---------------------------------------------------------------- 外观
    def _root_set(self, name: str, value: object) -> None:
        self._root.setProperty(name, value)

    @property
    def line_width(self) -> float:
        return float(self._root.property("lineWidth"))

    @line_width.setter
    def line_width(self, value: float) -> None:
        self._root_set("lineWidth", float(value))

    @property
    def curve_color(self) -> str:
        return str(self._root.property("curveColor"))

    @curve_color.setter
    def curve_color(self, value: str) -> None:
        self._root_set("curveColor", value)

    @property
    def background_color(self) -> str:
        return str(self._root.property("backgroundColor"))

    @background_color.setter
    def background_color(self, value: str) -> None:
        self._root_set("backgroundColor", value)

    # ------------------------------------------------------------------ 视图
    def view_bounds(self) -> tuple[float, float, float, float]:
        """当前视图 (xmin, xmax, ymin, ymax)，世界坐标。"""
        v = self._controller.view
        return (v.x(), v.y(), v.z(), v.w())

    def reset_view(self) -> None:
        self._controller.resetView()

    def zoom(self, delta: float, u: float = 0.5, v: float = 0.5) -> None:
        """以归一化锚点 (u, v) 缩放；delta 为滚轮增量（120 = 一档，正 = 放大）。"""
        self._controller.zoom(delta, u, v)

    def pan_pixels(self, dx: float, dy: float) -> None:
        """按像素平移（屏幕坐标，y 向下）。"""
        self._controller.panPixels(dx, dy, float(self._quick.width()), float(self._quick.height()))
