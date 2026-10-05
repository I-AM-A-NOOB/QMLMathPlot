"""ViewModel 层：把 Model（表达式 -> GLSL -> .qsb、视图数学）包成 QObject。

QML 侧（View）只做画和输入：表达式、视图、着色器 URL、错误文本都由这里暴露，
鼠标/滚轮事件转发成 zoom/panPixels 调用。嵌入到别的 App 时，由 App 创建
PlotController 并注入组件（见 qml_component_path 与 README）。
"""

from __future__ import annotations

import sympy as sp
from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QVector4D

from .model import ViewRect, shader_sources
from .qsb import bake

__all__ = ["QML_URI", "PlotController", "qml_component_path", "register_qml_types"]

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
        self._view = ViewRect()
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

    # 注解写值类型（PySide6 的惯用法）：Property 是描述符，不注解的话类型检查器
    # 只看到 Property 对象，Python 侧读写都会被判成类型错误。
    expression: str = Property(str, _get_expression, _set_expression, notify=expressionChanged)

    # ------------------------------------------------------------ 着色器 URL
    def _get_vertex_shader(self) -> QUrl:
        return self._vertex

    def _get_fragment_shader(self) -> QUrl:
        return self._fragment

    vertexShader: QUrl = Property(QUrl, _get_vertex_shader, notify=shadersChanged)
    fragmentShader: QUrl = Property(QUrl, _get_fragment_shader, notify=shadersChanged)

    def _get_error(self) -> str:
        return self._error

    error: str = Property(str, _get_error, notify=errorChanged)

    def _compile(self, source: str) -> None:
        """表达式 -> GLSL -> .qsb；失败时保留上一份可用着色器并把原因写进 error。"""
        try:
            expr = sp.sympify(source, locals={"x": self._symbol})
            vert_src, frag_src = shader_sources(expr)
            vertex = bake(vert_src, "vert")
            fragment = bake(frag_src, "frag")
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

    view: QVector4D = Property(QVector4D, _get_view, notify=viewChanged)

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


def qml_component_path() -> str:
    """可复用 QML 组件的文件路径（嵌入到别的 App 时用它 setSource / Loader）。"""
    from importlib.resources import files

    return str(files("qmlmathplot").joinpath("qml/MathPlot.qml"))
