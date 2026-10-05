"""QMLMathPlot —— 可嵌入的函数绘图组件（Qt Quick / QtWidgets 都能用）。

最省事的用法（QtWidgets 布局里一行）：

    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    from qmlmathplot import MathPlotWidget

    app = QApplication([])
    window = QWidget()
    box = QVBoxLayout(window)
    plot = MathPlotWidget("sin(1/x)")
    box.addWidget(plot)
    window.show()
    app.exec()

独立窗口 / 命令行：

    uv run qmlmathplot "sin(1/x)"          # 见 qmlmathplot.app
    python examples/minimal.py "tan(x)"    # 同上
    python examples/explorer.py            # 输入框 + 其他控件共存验证

纯 QML（Qt Quick）App 里用组件 + ViewModel（MVVM，分三层各自可测）：

    Model       model.py       表达式 -> GLSL、着色器源码、视图矩形数学（不依赖 Qt）
    ViewModel   viewmodel.py   PlotController：expression / view / 着色器 URL / error
    View        qml/MathPlot.qml   纯 QML 组件，注入 ViewModel 即可用

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickView
    from qmlmathplot import PlotController, qml_component_path, register_qml_types

    app = QGuiApplication([])
    register_qml_types()
    view = QQuickView()
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    view.rootObject().setProperty("controller", PlotController("sin(1/x)"))
    view.show()
    app.exec()

``MathPlotWidget`` 走**延迟导入**：纯 QML 的用法不会为此多加载 QtWidgets。
"""

from typing import TYPE_CHECKING

from .model import (
    DEFAULT_VIEW,
    FRAGMENT_TEMPLATE,
    VERTEX_SHADER,
    ViewRect,
    dfunc_glsl,
    func_glsl,
    shader_sources,
)
from .viewmodel import QML_URI, PlotController, qml_component_path, register_qml_types

if TYPE_CHECKING:  # 只给类型检查器看；运行时由模块级 __getattr__ 延迟导入
    from .widget import MathPlotWidget

__all__ = [
    "DEFAULT_VIEW",
    "FRAGMENT_TEMPLATE",
    "PlotController",
    "QML_URI",
    "VERTEX_SHADER",
    "ViewRect",
    "dfunc_glsl",
    "func_glsl",
    "qml_component_path",
    "register_qml_types",
    "shader_sources",
    "MathPlotWidget",
]


def __getattr__(name: str) -> object:
    """``MathPlotWidget`` 延迟导入（省掉纯 QML 用法不需要的 QtWidgets 开销）。"""
    if name == "MathPlotWidget":
        from .widget import MathPlotWidget

        return MathPlotWidget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
