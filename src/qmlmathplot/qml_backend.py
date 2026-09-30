"""QML 前端的模型层：把"表达式 -> GLSL -> .qsb"和视图数学包成 QObject。

QML 侧只管画（ShaderEffect + 输入），表达式编译、着色器烘焙、视图数学都在这，
和 QWidget 前端共用 qmlmathplot.core。
"""

from __future__ import annotations

import sympy as sp
from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QVector4D

from . import core, qsb

__all__ = ["PlotController", "register_qml_types", "QML_URI"]

QML_URI = "QmlMathPlot"
QML_MAJOR = 1
QML_MINOR = 0


class PlotController(QObject):
    """表达式 + 视图 + 着色器 URL，给 QML 绑定用。"""

    expressionChanged = Signal()
    viewChanged = Signal()
    shadersChanged = Signal()
    errorChanged = Signal()

    def __init__(self, expression: str = "sin(x)", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._symbol = sp.Symbol("x")
        self._expression = expression
        self._view = core.ViewRect()
        self._error = ""
        self._vertex = QUrl()
        self._fragment = QUrl()
        self._compile(expression)

    # ---------------------------------------------------------------- 表达式
    def _get_expression(self) -> str:
        return self._expression

    def _set_expression(self, source: str) -> None:
        if source == self._expression:
            return
        self._expression = source
        self.expressionChanged.emit()
        self._compile(source)

    expression = Property(str, _get_expression, _set_expression, notify=expressionChanged)

    # ------------------------------------------------------------ 着色器 URL
    def _get_vertex_shader(self) -> QUrl:
        return self._vertex

    def _get_fragment_shader(self) -> QUrl:
        return self._fragment

    vertexShader = Property(QUrl, _get_vertex_shader, notify=shadersChanged)
    fragmentShader = Property(QUrl, _get_fragment_shader, notify=shadersChanged)

    def _get_error(self) -> str:
        return self._error

    error = Property(str, _get_error, notify=errorChanged)

    def _compile(self, source: str) -> None:
        """表达式 -> GLSL -> .qsb；失败时保留上一份可用着色器并把原因写进 error。"""
        try:
            expr = sp.sympify(source, locals={"x": self._symbol})
            vert_src, frag_src = core.qml_shader_sources(expr)
            vertex = qsb.bake(vert_src, "vert")
            fragment = qsb.bake(frag_src, "frag")
        except Exception as exc:  # noqa: BLE001 —— 表达式/烘焙都可能失败，都要报给 UI
            self._error = f"{type(exc).__name__}: {exc}"
            self.errorChanged.emit()
            return
        self._error = ""
        self._vertex = QUrl.fromLocalFile(str(vertex))
        self._fragment = QUrl.fromLocalFile(str(fragment))
        self.errorChanged.emit()
        self.shadersChanged.emit()

    # ------------------------------------------------------------------ 视图
    def _get_view(self) -> QVector4D:
        return QVector4D(*self._view.as_tuple())

    view = Property(QVector4D, _get_view, notify=viewChanged)

    @Slot(float, float, float)
    def zoom(self, delta: float, u: float, v: float) -> None:
        """以归一化位置 (u, v) 为锚点缩放，delta 为滚轮增量（120 = 一档）。"""
        self._view.zoom(delta, u, v)
        self.viewChanged.emit()

    @Slot(float, float, float, float)
    def panPixels(self, dx: float, dy: float, width: float, height: float) -> None:
        self._view.pan_pixels(dx, dy, width, height)
        self.viewChanged.emit()

    @Slot()
    def resetView(self) -> None:
        self._view.reset()
        self.viewChanged.emit()


def register_qml_types() -> None:
    """把 PlotController 注册成 QML 类型（引擎 load 之前调用）。"""
    from PySide6.QtQml import qmlRegisterType

    # 注意：PySide6 的签名标注写的是 bytes，但运行时只接受 str
    qmlRegisterType(PlotController, QML_URI, QML_MAJOR, QML_MINOR, "PlotController")  # type: ignore[arg-type]
