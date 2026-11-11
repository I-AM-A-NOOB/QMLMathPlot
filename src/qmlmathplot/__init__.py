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

    import QmlMathPlot 2.0
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

if TYPE_CHECKING:  # only for type checkers; at runtime the module-level __getattr__ is used
    from .camera import HOME_SIZE, HOME_VIEW, Camera, nice_ticks
    from .curve import Curve, CurveListModel, ImplicitCurve
    from .input import ViewInput
    from .overlay import Annotation, Guide
    from .plot import Plot
    from .qsb import cache_dir, cache_info, clear_cache, prune_cache
    from .view import QML_MAJOR, QML_MINOR, QML_URI, qml_component_path, register_qml_types
    from .widget import MathPlotWidget

__all__ = [
    "Annotation",
    "cache_dir",
    "cache_info",
    "Camera",
    "clear_cache",
    "Curve",
    "CurveListModel",
    "Guide",
    "HOME_SIZE",
    "HOME_VIEW",
    "ImplicitCurve",
    "MathPlotWidget",
    "nice_ticks",
    "Plot",
    "Program",
    "ProgramSource",
    "ProgramSourceMissingError",
    "prune_cache",
    "qml_component_path",
    "QML_MAJOR",
    "QML_MINOR",
    "QML_URI",
    "register_qml_types",
    "ViewInput",
]


#: name -> (module, attribute). ``None`` for the attribute means "the module itself".
_LAZY: dict[str, tuple[str, str | None]] = {
    "Annotation": (".overlay", "Annotation"),
    "cache_dir": (".qsb", "cache_dir"),
    "cache_info": (".qsb", "cache_info"),
    "Camera": (".camera", "Camera"),
    "clear_cache": (".qsb", "clear_cache"),
    "Curve": (".curve", "Curve"),
    "CurveListModel": (".curve", "CurveListModel"),
    "Guide": (".overlay", "Guide"),
    "HOME_SIZE": (".camera", "HOME_SIZE"),
    "HOME_VIEW": (".camera", "HOME_VIEW"),
    "ImplicitCurve": (".curve", "ImplicitCurve"),
    "MathPlotWidget": (".widget", "MathPlotWidget"),
    "nice_ticks": (".camera", "nice_ticks"),
    "Plot": (".plot", "Plot"),
    "Program": (".protocol", "Program"),
    "ProgramSource": (".protocol", "ProgramSource"),
    "ProgramSourceMissingError": (".protocol", "ProgramSourceMissingError"),
    "prune_cache": (".qsb", "prune_cache"),
    "qml_component_path": (".view", "qml_component_path"),
    "QML_MAJOR": (".view", "QML_MAJOR"),
    "QML_MINOR": (".view", "QML_MINOR"),
    "QML_URI": (".view", "QML_URI"),
    "register_qml_types": (".view", "register_qml_types"),
    "ViewInput": (".input", "ViewInput"),
}


def __getattr__(name: str) -> object:
    """Resolve public names lazily.

    Nothing is imported at package-import time: ``import qmlmathplot`` must not load Qt or
    sympy, so a host that only needs the calculator protocol (or a QML-only app) pays for
    nothing it does not use. The same laziness keeps the calculator process free of Qt.
    """
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(target[0], __name__)
    value = module if target[1] is None else getattr(module, target[1])
    globals()[name] = value
    return value
