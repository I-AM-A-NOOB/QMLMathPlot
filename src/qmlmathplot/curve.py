"""Curve layer: one expression -> GLSL -> .qsb plus its style, and the list model QML repeats.

A curve knows nothing about the view: the camera decides where we look, the curve decides
what is drawn. One curve = one fragment shader pass (see ``model.FRAGMENT_TEMPLATE``), so
``visible`` is the lever that costs nothing.
"""

from __future__ import annotations

import sympy as sp
from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor

from .model import shader_sources
from .qsb import bake

__all__ = ["Curve", "CurveListModel"]

#: First colour of the default cycle (matplotlib's "tab10" blue, as in its default style).
DEFAULT_COLOR = "#1f77b4"
#: matplotlib's default ten-colour cycle.
DEFAULT_COLOR_CYCLE = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                       "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf")


def _color(value: object) -> QColor:
    """A QColor from a QColor or anything QColor() understands ("#rrggbb", a name, …)."""
    return QColor(value) if not isinstance(value, QColor) else QColor(value)


class Curve(QObject):
    """One curve: an expression, its baked shaders, its style and its error text."""

    expressionChanged = Signal()
    styleChanged = Signal()
    visibleChanged = Signal()
    labelChanged = Signal()
    errorChanged = Signal()
    shadersChanged = Signal()

    def __init__(
        self,
        expression: str | sp.Expr = "sin(x)",
        color: str | QColor = DEFAULT_COLOR,
        line_width: float = 1.5,
        label: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._symbol = sp.Symbol("x")
        self._expression = ""
        self._expr: sp.Expr = sp.Integer(0)      # replaced by the first assignment below
        self._color = _color(color)
        self._line_width = float(line_width)
        self._label = str(label)
        self._visible = True
        self._error = ""
        self._vertex = QUrl()
        self._fragment = QUrl()
        self._set_expression(expression)

    # ---------------------------------------------------------------- Expression
    def _get_expression(self) -> str:
        return self._expression

    def _set_expression(self, source: str | sp.Expr) -> None:
        """Set the expression from a string (parsed here, once) or from a sympy expression.

        The canonical **string** is what identifies the expression — ``sp.sin(x)`` and
        ``"sin(x)"`` are the same, and re-setting either is free — while the parsed
        ``sp.Expr`` is cached for the bake and for the matplotlib hand-off, so neither ever
        stringifies and re-parses again.
        """
        canonical = source if isinstance(source, str) else str(source)
        if canonical == self._expression:
            return
        if isinstance(source, sp.Basic):          # already an expression: use it as it is
            expr: sp.Expr = source
        else:
            try:
                expr = sp.sympify(source, locals={"x": self._symbol})
            except Exception as exc:
                # Keep the last parsed expression: the shaders stay, `expression` is what was
                # typed and `error` says why.
                self._expression = str(canonical)
                self._error = f"{type(exc).__name__}: {exc}"
                self.expressionChanged.emit()
                self.errorChanged.emit()
                return
        self._expression = str(canonical)
        self._expr = expr
        self.expressionChanged.emit()
        self._compile(expr)

    # Annotate the value type (PySide6 idiom): Property is a descriptor, so without the
    # annotation a type checker only sees the Property object and flags every read and write.
    # Declared "QVariant" so an ``sp.Expr`` reaches the setter unconverted; the getter is always
    # the canonical string (QML, a project file and the text field all want one).
    expression: str | sp.Expr = Property("QVariant", _get_expression, _set_expression,
                                         notify=expressionChanged)

    def _get_expr(self) -> sp.Expr:
        return self._expr

    #: The parsed expression currently drawn (read-only): the object form the setter accepts.
    expr: sp.Expr = Property("QVariant", _get_expr, notify=expressionChanged)

    # ------------------------------------------------------------------ Style
    def _get_color(self) -> QColor:
        return QColor(self._color)

    def _set_color(self, value: object) -> None:
        color = _color(value)
        if color == self._color:
            return
        self._color = color
        self.styleChanged.emit()

    color: QColor = Property(QColor, _get_color, _set_color, notify=styleChanged)

    def _get_line_width(self) -> float:
        return self._line_width

    def _set_line_width(self, value: float) -> None:
        value = float(value)
        if value == self._line_width:
            return
        self._line_width = value
        self.styleChanged.emit()

    lineWidth: float = Property(float, _get_line_width, _set_line_width, notify=styleChanged)

    def _get_visible(self) -> bool:
        return self._visible

    def _set_visible(self, value: bool) -> None:
        value = bool(value)
        if value == self._visible:
            return
        self._visible = value
        self.visibleChanged.emit()

    visible: bool = Property(bool, _get_visible, _set_visible, notify=visibleChanged)

    def _get_label(self) -> str:
        return self._label

    def _set_label(self, value: str) -> None:
        value = str(value)
        if value == self._label:
            return
        self._label = value
        self.labelChanged.emit()

    label: str = Property(str, _get_label, _set_label, notify=labelChanged)

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

    def _compile(self, expr: sp.Expr) -> None:
        """expr -> GLSL -> .qsb; on failure keep the last working shaders and write the reason
        into ``error``. The expression is parsed by the setter, never here."""
        try:
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


class CurveListModel(QAbstractListModel):
    """The plot's curves, as a model so QML can put one ShaderEffect behind each of them."""

    CurveRole = int(Qt.ItemDataRole.UserRole) + 1
    ExpressionRole = CurveRole + 1
    ColorRole = CurveRole + 2
    LineWidthRole = CurveRole + 3
    VisibleRole = CurveRole + 4
    VertexShaderRole = CurveRole + 5
    FragmentShaderRole = CurveRole + 6

    _ROLES = {
        CurveRole: b"curve",
        ExpressionRole: b"expression",
        ColorRole: b"color",
        LineWidthRole: b"lineWidth",
        VisibleRole: b"visible",
        VertexShaderRole: b"vertexShader",
        FragmentShaderRole: b"fragmentShader",
    }

    def __init__(
        self,
        color_cycle: tuple[str, ...] = DEFAULT_COLOR_CYCLE,
        default_line_width: float = 1.5,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._curves: list[Curve] = []
        self._cycle = [str(entry) for entry in color_cycle] or [DEFAULT_COLOR]
        self._default_line_width = float(default_line_width)

    # ------------------------------------------------------------ model glue
    def roleNames(self) -> dict[int, bytes]:  # noqa: N802 (Qt naming)
        return dict(self._ROLES)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._curves)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._curves):
            return None
        curve = self._curves[index.row()]
        if role == self.CurveRole:
            return curve
        if role == self.ExpressionRole:
            return curve.expression
        if role == self.ColorRole:
            return curve.color
        if role == self.LineWidthRole:
            return curve.lineWidth
        if role == self.VisibleRole:
            return curve.visible
        if role == self.VertexShaderRole:
            return curve.vertexShader
        if role == self.FragmentShaderRole:
            return curve.fragmentShader
        return None

    def _notify(self, curve: Curve, roles: tuple[int, ...]) -> None:
        row = self.index_of(curve)
        if row < 0:
            return
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, list(roles))

    def _watch(self, curve: Curve) -> None:
        curve.expressionChanged.connect(
            lambda c=curve: self._notify(c, (self.ExpressionRole,))
        )
        curve.shadersChanged.connect(
            lambda c=curve: self._notify(c, (self.VertexShaderRole, self.FragmentShaderRole))
        )
        curve.styleChanged.connect(
            lambda c=curve: self._notify(c, (self.ColorRole, self.LineWidthRole))
        )
        curve.visibleChanged.connect(lambda c=curve: self._notify(c, (self.VisibleRole,)))

    # ---------------------------------------------------------------- public
    # "QVariant" so a sympy expression is not stringified on the way in (see Plot.add_curve).
    @Slot("QVariant", "QVariant", "QVariant", str, result=QObject)
    @Slot("QVariant", result=QObject)
    def add_curve(
        self,
        expression: "str | sp.Expr",
        color: object = None,
        line_width: object = None,
        label: str = "",
    ) -> Curve:
        """Append a curve. Colour and line width default to the plot's cycle and width."""
        if color is None:
            color = self._cycle[len(self._curves) % len(self._cycle)]
        if line_width is None:
            line_width = self._default_line_width
        curve = Curve(expression, color, float(line_width), label, parent=self)  # type: ignore[arg-type]
        row = len(self._curves)
        self.beginInsertRows(QModelIndex(), row, row)
        self._curves.append(curve)
        self.endInsertRows()
        self._watch(curve)
        return curve

    @Slot(QObject)
    def remove_curve(self, curve: Curve) -> None:
        row = self.index_of(curve)
        if row < 0:
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._curves[row]
        self.endRemoveRows()

    @Slot(int, result=QObject)
    def at(self, row: int) -> Curve:
        return self._curves[row]

    def index_of(self, curve: Curve) -> int:
        """Row of ``curve``, or -1 (QML calls go through ``at``; this one is for Python)."""
        for row, candidate in enumerate(self._curves):
            if candidate is curve:
                return row
        return -1

    def set_color_cycle(self, color_cycle: tuple[str, ...]) -> None:
        """Colours handed out by ``add_curve`` (the plot's ``colorCycle``)."""
        self._cycle = [str(entry) for entry in color_cycle] or [DEFAULT_COLOR]

    def set_default_line_width(self, width: float) -> None:
        self._default_line_width = float(width)

    def __len__(self) -> int:
        return len(self._curves)
