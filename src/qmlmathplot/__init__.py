"""QMLMathPlot — an embeddable function plotting widget (works with Qt Quick and QtWidgets).

Quickest usage (one line in a QtWidgets layout):

    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    from qmlmathplot import MathPlotWidget

    app = QApplication([])
    window = QWidget()
    box = QVBoxLayout(window)
    plot = MathPlotWidget("sin(1/x)")
    box.addWidget(plot)
    window.show()
    app.exec()

Standalone window / command line:

    uv run qmlmathplot "sin(1/x)"            # see qmlmathplot.app
    python examples/minimal.py "tan(x)"      # same
    python examples/explorer_qtwidgets.py    # input field coexisting with other widgets

Pure QML (Qt Quick) apps use the component plus the ViewModel (MVVM, three layers,
each testable on its own):

    Model       model.py        expression -> GLSL, shader sources, view rectangle math (no Qt)
    ViewModel   viewmodel.py    PlotController: expression / view / shader URLs / error
    View        qml/MathPlot.qml    pure QML component; inject the ViewModel to use it

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

``MathPlotWidget`` is imported **lazily**: pure QML usage does not pull in QtWidgets for it.
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

if TYPE_CHECKING:  # type checkers only; at runtime the lazy import happens via module __getattr__
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
    """Lazily import ``MathPlotWidget`` (avoids the QtWidgets cost pure QML usage doesn't need)."""
    if name == "MathPlotWidget":
        from .widget import MathPlotWidget

        return MathPlotWidget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
