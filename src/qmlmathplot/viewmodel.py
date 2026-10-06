"""ViewModel layer: wraps the Model (expression -> GLSL -> .qsb, view math) as a QObject.

The QML side (View) only draws and handles input: expression, view, shader URLs and error text
are all exposed here, and mouse/wheel events are forwarded as zoom/panPixels calls. When
embedding into another app, the app creates the PlotController and injects it into the component
(see qml_component_path and the README).
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
    """Expression + view + shader URLs, for QML bindings."""

    expressionChanged = Signal()
    viewChanged = Signal()
    aspectChanged = Signal()
    shadersChanged = Signal()
    errorChanged = Signal()

    def __init__(self, expression: str = "sin(x)", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._symbol = sp.Symbol("x")
        self._expression = expression
        self._view = ViewRect()
        self._home = ViewRect()                 # the configured default view (reset target)
        self._aspect: str | float = "view"      # "view" = follow the widget, or a number
        self._viewport: tuple[float, float] = (800.0, 600.0)
        self._viewport_known = False            # the first report only sets the baseline
        self._error = ""
        self._vertex = QUrl()
        self._fragment = QUrl()
        self._compile(expression)

    # ---------------------------------------------------------------- Expression
    def _get_expression(self) -> str:
        return self._expression

    def _set_expression(self, source: str) -> None:
        if source == self._expression:
            return
        self._expression = source
        self.expressionChanged.emit()
        self._compile(source)

    # Annotate the value type (PySide6 idiom): Property is a descriptor, so without the
    # annotation a type checker only sees the Property object and flags every Python-side read
    # and write as a type error.
    expression: str = Property(str, _get_expression, _set_expression, notify=expressionChanged)

    # ------------------------------------------------------------- Shader URLs
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
        """expression -> GLSL -> .qsb; on failure keep the last working shaders and write the
        reason into error."""
        try:
            expr = sp.sympify(source, locals={"x": self._symbol})
            vert_src, frag_src = shader_sources(expr)
            vertex = bake(vert_src, "vert")
            fragment = bake(frag_src, "frag")
        except Exception as exc:  # noqa: BLE001 — expression or bake can fail; both reach the UI
            self._error = f"{type(exc).__name__}: {exc}"
            self.errorChanged.emit()
            return
        self._error = ""
        self._vertex = QUrl.fromLocalFile(str(vertex))
        self._fragment = QUrl.fromLocalFile(str(fragment))
        self.errorChanged.emit()
        self.shadersChanged.emit()

    # ------------------------------------------------------------------ View
    def _apply_aspect(self) -> None:
        """Write the aspect adjustment back into the limits (expand only, never crop).

        The invariant of the whole view is: ``xlim``/``ylim`` *are* the visible range. The
        aspect is the only thing the library may change about them, and it does so by
        adjusting them — never by keeping a second, hidden range.
        """
        aspect = self._aspect_value()
        if aspect is None:
            return
        width, height = self._viewport
        bounds = self._view.effective(width, height, aspect)
        self._view.xmin, self._view.xmax, self._view.ymin, self._view.ymax = bounds

    def _aspect_value(self) -> float | None:
        """The aspect as a number, or None for "follow the view"."""
        if isinstance(self._aspect, str):
            return None
        value = float(self._aspect)
        return value if value > 0 else None

    def _get_aspect(self) -> str | float:
        return self._aspect

    def _set_aspect(self, value: str | float) -> None:
        if isinstance(value, str) and value != "view":
            raise ValueError(f'aspect must be "view" or a positive number, got {value!r}')
        if not isinstance(value, str) and float(value) <= 0:
            raise ValueError(f"aspect must be positive, got {value!r}")
        if value == self._aspect:
            return
        self._aspect = value
        self._apply_aspect()            # keep xlim/ylim == what is drawn
        self.aspectChanged.emit()
        self.viewChanged.emit()

    aspect: str | float = Property("QVariant", _get_aspect, _set_aspect, notify=aspectChanged)

    @Slot(float, float)
    def setViewport(self, width: float, height: float) -> None:
        """Tell the controller the plot's size in logical pixels (the QML item calls this on
        resize).

        With a fixed aspect ratio this keeps the **scale** — world units per pixel — and lets
        the visible range follow the widget, so nothing zooms while a window or a splitter is
        dragged; the range grows or shrinks proportionally instead. ``"view"`` mode keeps the
        range as it is (the shape then follows the widget). The first report only records the
        baseline size, so the startup view is not scaled.
        """
        if width <= 0 or height <= 0:
            return
        if not self._viewport_known:
            # First real size. The aspect may already have been applied against the *assumed*
            # size, so re-derive the view from the home limits for the real size — expanding a
            # stale result would leave the range off by the assumed/real ratio.
            self._viewport = (width, height)
            self._viewport_known = True
            bounds = self._home.effective(width, height, self._aspect_value())
            self._view.xmin, self._view.xmax, self._view.ymin, self._view.ymax = bounds
            self.viewChanged.emit()
            return
        if (width, height) == self._viewport:
            return
        old_w, _old_h = self._viewport
        aspect = self._aspect_value()
        if aspect is not None:
            # Keep the scale (world units per pixel) and the centre, so the curve never zooms
            # while a window or a splitter is dragged; the aspect then fixes the other span.
            span_x = (self._view.xmax - self._view.xmin) * (width / old_w)
            cx = 0.5 * (self._view.xmin + self._view.xmax)
            cy = 0.5 * (self._view.ymin + self._view.ymax)
            span_y = height * span_x / (width * aspect)
            self._view.xmin, self._view.xmax = cx - 0.5 * span_x, cx + 0.5 * span_x
            self._view.ymin, self._view.ymax = cy - 0.5 * span_y, cy + 0.5 * span_y
        self._viewport = (width, height)
        self._viewport_known = True
        self.viewChanged.emit()

    def _get_view(self) -> QVector4D:
        # The limits are the visible range (see _apply_aspect), so this is a plain read.
        return QVector4D(*self._view.as_tuple())

    view: QVector4D = Property(QVector4D, _get_view, notify=viewChanged)

    @Slot(float, float, float)
    def zoom(self, delta: float, u: float, v: float) -> None:
        """Zoom anchored at the normalized position (u, v); delta is the wheel step (120 = one
        notch)."""
        self._view.zoom(delta, u, v)        # zooming scales both spans: the aspect holds
        self.viewChanged.emit()

    @Slot(float, float, float, float)
    def panPixels(self, dx: float, dy: float, width: float, height: float) -> None:
        self._view.pan_pixels(dx, dy, width, height)
        self.viewChanged.emit()

    @Slot()
    def resetView(self) -> None:
        self._view.reset()
        self.viewChanged.emit()


_registered = False


def register_qml_types() -> None:
    """Register the QML types of this package (call before the engine loads).

    Registers both ``PlotController`` (the ViewModel) and ``MathPlot`` (the View, i.e. the
    ``qml/MathPlot.qml`` component), so a Qt Quick app can simply write::

        import QmlMathPlot 1.0
        MathPlot { anchors.fill: parent }

    Idempotent: repeated calls are a no-op (Qt complains about duplicate registrations).
    """
    global _registered
    if _registered:
        return

    from PySide6.QtQml import qmlRegisterType

    # Note: PySide6's signature annotation says bytes, but at runtime it only accepts str
    qmlRegisterType(PlotController, QML_URI, QML_MAJOR, QML_MINOR, "PlotController")  # type: ignore[arg-type]
    qmlRegisterType(QUrl.fromLocalFile(qml_component_path()), QML_URI, QML_MAJOR, QML_MINOR, "MathPlot")
    _registered = True


def qml_component_path() -> str:
    """File path of the reusable QML component (for setSource / Loader when embedding it into
    another app)."""
    from importlib.resources import files

    return str(files("qmlmathplot").joinpath("qml/MathPlot.qml"))
