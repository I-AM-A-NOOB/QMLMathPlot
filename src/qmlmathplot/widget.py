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

The widget keeps one curve's worth of convenience (``expression``, ``line_width``,
``curve_color``) for the common case; everything else lives on the model, reachable as
``plot`` — ``plot.curves`` for more curves, ``plot.camera`` for the view.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from .curve import Curve
from .plot import Plot
from .view import qml_component_path, register_qml_types

__all__ = ["MathPlotWidget"]


class MathPlotWidget(QWidget):
    """Function plotting widget (QWidget version).

    :param expression: expression in sympy syntax, ``"sin(x)"`` by default
    :param line_width: line width (logical pixels); None keeps the plot's own default
    :param curve_color: curve color (``#rrggbb``); None keeps the plot's own default
    :param background_color: background color (``#rrggbb``); None keeps the plot's own default
    :param aspect: ``"auto"`` (default) or the ratio of the y scale to the x scale

    The signals ``expressionChanged`` / ``errorChanged`` stay in sync with the first curve; a
    non-empty ``error`` means the expression failed to parse (the last working drawing is kept).
    """

    expressionChanged = Signal()
    errorChanged = Signal()

    def __init__(
        self,
        expression: str = "sin(x)",
        parent: QWidget | None = None,
        *,
        line_width: float | None = None,
        curve_color: str | None = None,
        background_color: str | None = None,
        aspect: str | float = "auto",
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
        self._plot = Plot(self)
        # None for a style argument means "keep the plot's own default" (which the plot takes
        # from its theme), so the widget does not re-state matplotlib's numbers.
        if background_color is not None:
            self._plot.background = background_color
        if line_width is not None:
            self._plot.line_width = float(line_width)
        self._curve = self._plot.add_curve(expression, color=curve_color, line_width=line_width)
        self._curve.expressionChanged.connect(self.expressionChanged)
        self._curve.errorChanged.connect(self.errorChanged)
        self._root.setProperty("plot", self._plot)
        self.aspect = aspect

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self._quick)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Report the plot's real size to the camera.

        The QML component reports it too, but for the widget path those reports can fire while
        the component is still being created — before the injected plot is in place — so the
        widget reports the authoritative size itself.
        """
        super().resizeEvent(event)
        self._plot.camera.setViewport(float(self._quick.width()), float(self._quick.height()))

    # ------------------------------------------------------------------ Model
    @property
    def plot(self) -> Plot:
        """The underlying model: camera, curves and styling."""
        return self._plot

    @property
    def curve(self) -> Curve:
        """The first curve, which the single-curve conveniences below drive."""
        return self._curve

    @property
    def expression(self) -> str:
        return self._curve.expression

    @expression.setter
    def expression(self, source: str) -> None:
        self._curve.expression = source

    @property
    def error(self) -> str:
        """Most recent parse error (empty string = OK)."""
        return self._curve.error

    # ---------------------------------------------------------------- Appearance
    @property
    def line_width(self) -> float:
        return self._curve.lineWidth

    @line_width.setter
    def line_width(self, value: float) -> None:
        self._plot.line_width = float(value)     # default for curves added later
        self._curve.lineWidth = float(value)

    @property
    def curve_color(self) -> str:
        return self._curve.color.name()

    @curve_color.setter
    def curve_color(self, value: str) -> None:
        self._curve.color = value

    @property
    def background_color(self) -> str:
        return self._plot.background.name()

    @background_color.setter
    def background_color(self, value: str) -> None:
        self._plot.background = value

    @property
    def aspect(self) -> str | float:
        """``"auto"`` (default) lets the shape follow the widget's aspect ratio; a number
        keeps the ratio of the y scale to the x scale fixed (``1.0`` = square units)."""
        return self._plot.camera.aspect

    @aspect.setter
    def aspect(self, value: str | float) -> None:
        self._plot.camera.aspect = value

    # ------------------------------------------------------------------ View
    def view_bounds(self) -> tuple[float, float, float, float]:
        """Current view (xmin, xmax, ymin, ymax) in world coordinates."""
        xlim = self._plot.camera.xlim
        ylim = self._plot.camera.ylim
        return (xlim.x(), xlim.y(), ylim.x(), ylim.y())

    def reset_view(self) -> None:
        self._plot.camera.reset()

    def zoom(self, delta: float, u: float = 0.5, v: float = 0.5) -> None:
        """Zoom anchored at the normalized point (u, v); delta is the wheel step (120 = one
        notch, positive = zoom in)."""
        self._plot.camera.zoom_by(
            delta, u, v, float(self._quick.width()), float(self._quick.height())
        )

    def pan_pixels(self, dx: float, dy: float) -> None:
        """Pan by pixels (screen coordinates, y pointing down)."""
        self._plot.camera.pan_pixels(dx, dy)
