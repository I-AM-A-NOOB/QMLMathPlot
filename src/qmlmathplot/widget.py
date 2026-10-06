"""Wrap the plotting component as an ordinary QWidget, droppable into any QtWidgets layout.

    from qmlmathplot import MathPlotWidget

    plot = MathPlotWidget("sin(1/x)")
    layout.addWidget(plot)
    plot.expression = "tan(x)"       # change the expression
    plot.reset_view()

The QML component (View) is itself a Qt Quick Item; ``QQuickWidget`` bridges it into the widgets
world: it renders into its own FBO, so it stacks and coexists properly with sibling widgets
(input fields, scroll areas, splitters, overlays); wheel and left-drag are already ``accepted``
on the QML side, so they never leak to a parent scroll area.

A ``QApplication`` must exist first (QQuickWidget belongs to QtWidgets); ``register_qml_types()``
is called automatically here and is safe to call repeatedly.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from .viewmodel import PlotController, qml_component_path, register_qml_types

__all__ = ["MathPlotWidget"]


class MathPlotWidget(QWidget):
    """Function plotting widget (QWidget version).

    :param expression: expression in sympy syntax, ``"sin(x)"`` by default
    :param line_width: line width (logical pixels)
    :param curve_color: curve color (``#rrggbb``)
    :param background_color: background color (``#rrggbb``)

    The signals ``expressionChanged`` / ``errorChanged`` stay in sync with the ViewModel; a
    non-empty ``error`` means the expression failed to parse (the last working drawing is kept).
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
        aspect: str | float = "view",
    ) -> None:
        # Check before super().__init__(): constructing a QWidget without a
        # QApplication aborts inside Qt, so the friendly error would never be reached.
        if QApplication.instance() is None:
            raise RuntimeError("please create a QApplication first — QQuickWidget needs QtWidgets")
        super().__init__(parent)
        register_qml_types()

        self._quick = QQuickWidget(self)
        self._quick.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        # Clicking the plot area should not enter the Tab focus chain (wheel/drag still handled
        # by QML as usual)
        self._quick.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._quick.setSource(QUrl.fromLocalFile(qml_component_path()))
        if self._quick.status() is not QQuickWidget.Status.Ready:
            raise RuntimeError(
                "QML component failed to load: " + "; ".join(e.toString() for e in self._quick.errors())
            )

        self._root = self._quick.rootObject()   # status is Ready, so this is always valid
        self._controller = PlotController(expression, self)
        self._root.setProperty("controller", self._controller)
        self._controller.expressionChanged.connect(self.expressionChanged)
        self._controller.errorChanged.connect(self.errorChanged)

        self._root_set("lineWidth", float(line_width))
        self._root_set("curveColor", curve_color)
        self._root_set("backgroundColor", background_color)
        self.aspect = aspect

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self._quick)

    # ------------------------------------------------------------- ViewModel
    @property
    def controller(self) -> PlotController:
        """The underlying ViewModel (for connecting more signals)."""
        return self._controller

    @property
    def expression(self) -> str:
        return self._controller.expression

    @expression.setter
    def expression(self, source: str) -> None:
        self._controller.expression = source

    @property
    def error(self) -> str:
        """Most recent parse error (empty string = OK)."""
        return self._controller.error

    # ---------------------------------------------------------------- Appearance
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
    def aspect(self) -> str | float:
        """``"view"`` (default) lets the shape follow the widget's aspect ratio; a number
        keeps the ratio of the y-unit to the x-unit fixed (``1.0`` = square units) by
        expanding the view instead of distorting the curve."""
        return self._controller.aspect

    @aspect.setter
    def aspect(self, value: str | float) -> None:
        self._controller.aspect = value

    @property
    def background_color(self) -> str:
        return str(self._root.property("backgroundColor"))

    @background_color.setter
    def background_color(self, value: str) -> None:
        self._root_set("backgroundColor", value)

    # ------------------------------------------------------------------ View
    def view_bounds(self) -> tuple[float, float, float, float]:
        """Current view (xmin, xmax, ymin, ymax) in world coordinates."""
        v = self._controller.view
        return (v.x(), v.y(), v.z(), v.w())

    def reset_view(self) -> None:
        self._controller.resetView()

    def zoom(self, delta: float, u: float = 0.5, v: float = 0.5) -> None:
        """Zoom anchored at the normalized point (u, v); delta is the wheel step (120 = one
        notch, positive = zoom in)."""
        self._controller.zoom(delta, u, v)

    def pan_pixels(self, dx: float, dy: float) -> None:
        """Pan by pixels (screen coordinates, y pointing down)."""
        self._controller.panPixels(dx, dy, float(self._quick.width()), float(self._quick.height()))
