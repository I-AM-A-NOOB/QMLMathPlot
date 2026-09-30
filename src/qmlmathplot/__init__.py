"""QMLMathPlot —— 可嵌入 Qt Quick App 的函数绘图组件（MVVM）。

分三层，各自独立可测：

    Model       model.py      表达式 -> GLSL、着色器源码、视图矩形数学（不依赖 Qt）
    ViewModel   viewmodel.py  PlotController：expression / view / 着色器 URL / error
    View        qml/MathPlot.qml  纯 QML 组件，注入 ViewModel 即可用

嵌入示例：

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
"""

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

__all__ = [
    "DEFAULT_VIEW",
    "FRAGMENT_TEMPLATE",
    "QML_URI",
    "VERTEX_SHADER",
    "PlotController",
    "ViewRect",
    "dfunc_glsl",
    "func_glsl",
    "qml_component_path",
    "register_qml_types",
    "shader_sources",
]
