"""QMLMathPlot — an embeddable function plotter for Qt (Qt Quick and QtWidgets).

The plot is an **infinite canvas with a camera**: the camera holds where you are looking
(centre, zoom, aspect) and the visible range follows from it, so resizing a widget shows more
or less canvas instead of distorting or re-zooming the curve. Curves are expressions evaluated
per pixel in a fragment shader, so the cost does not depend on the function's frequency.

QtWidgets layout in one line:

    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    from qmlmathplot import MathPlotWidget

    app = QApplication([])
    window = QWidget()
    box = QVBoxLayout(window)
    plot = MathPlotWidget("sin(1/x)")
    box.addWidget(plot)
    window.show()
    app.exec()

Qt Quick (QML) — the component plus the model:

    import QmlMathPlot 1.0
    PlotView { plot: myPlot; anchors.fill: parent }

    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickView
    from PySide6.QtCore import QUrl
    from qmlmathplot import Plot, qml_component_path, register_qml_types

    app = QGuiApplication([])
    register_qml_types()
    view = QQuickView()
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    view.rootObject().setProperty("plot", Plot())
    view.show()
    app.exec()

Model, without Qt widgets:

    plot = Plot()
    plot.xlim, plot.ylim = (-6.0, 6.0), (-2.0, 2.0)
    curve = plot.add_curve("sin(1/x)", label="sin(1/x)")
    curve.expression = "sin(2/x)"          # one signal -> re-bake -> the view swaps it
    plot.theme = "ggplot"                  # Matplotlib's style sheets, see qmlmathplot.themes

Demos: ``examples/minimal.py``, ``examples/explorer_qtwidgets.py``,
``examples/explorer_qtquick.py``. Design notes: ``docs/api-design.md``.
"""

from typing import TYPE_CHECKING

from .camera import HOME_SIZE, HOME_VIEW, Camera, nice_ticks
from .curve import Curve, CurveListModel
from .plot import Plot
from .view import QML_MAJOR, QML_MINOR, QML_URI, qml_component_path, register_qml_types

if TYPE_CHECKING:  # only for type checkers; at runtime the module-level __getattr__ is used
    from .widget import MathPlotWidget

__all__ = [
    "Camera",
    "Curve",
    "CurveListModel",
    "HOME_SIZE",
    "HOME_VIEW",
    "MathPlotWidget",
    "nice_ticks",
    "Plot",
    "qml_component_path",
    "QML_MAJOR",
    "QML_MINOR",
    "QML_URI",
    "register_qml_types",
]


def __getattr__(name: str) -> object:
    """``MathPlotWidget`` is imported lazily, so pure-QML use never loads QtWidgets."""
    if name == "MathPlotWidget":
        from .widget import MathPlotWidget

        return MathPlotWidget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
