"""Curve layer: one expression -> GLSL -> .qsb plus its style, and the list model QML repeats.

A curve knows nothing about the view: the camera decides where we look, the curve decides what is
drawn. One curve = one fragment shader pass, so ``visible`` is the lever that costs nothing.

Two artists share the class: ``Curve`` draws ``y = f(x)`` (see ``model.FRAGMENT_TEMPLATE``) and
``ImplicitCurve`` draws the level set ``F(x, y) = 0`` (``model.IMPLICIT_FRAGMENT_TEMPLATE``).
They differ only in the shader *sources* — the uniform block, the model roles and the QML side
are the same — so ``curve_for()`` can pick one from the expression's free symbols.
"""

from __future__ import annotations

import sympy as sp
from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QCoreApplication,
    QModelIndex,
    QObject,
    QRunnable,
    QThreadPool,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor

from .model import implicit_shader_sources, shader_sources
from .qsb import bake

__all__ = ["Curve", "CurveListModel", "ImplicitCurve", "curve_for", "parsed"]


class _BakeSignals(QObject):
    """The result channel of one background bake (a ``QRunnable`` cannot own signals)."""

    done = Signal(int, object)      # generation, (vertex, fragment) or an exception


class _BakeJob(QRunnable):
    """``expr -> GLSL -> qsb`` off the GUI thread (qsb is a subprocess)."""

    def __init__(
        self,
        signals: _BakeSignals,
        generation: int,
        vertex_source: str,
        fragment_source: str,
    ) -> None:
        super().__init__()
        self._signals = signals
        self._generation = generation
        self._vertex_source = vertex_source
        self._fragment_source = fragment_source

    def run(self) -> None:  # noqa: D401 - QRunnable API
        try:
            vertex = bake(self._vertex_source, "vert")
            fragment = bake(self._fragment_source, "frag")
        except Exception as exc:  # noqa: BLE001 - reported to the curve, never swallowed
            self._signals.done.emit(self._generation, exc)
        else:
            self._signals.done.emit(self._generation, (vertex, fragment))


#: First colour of the default cycle (matplotlib's "tab10" blue, as in its default style).
DEFAULT_COLOR = "#1f77b4"
#: matplotlib's default ten-colour cycle.
DEFAULT_COLOR_CYCLE = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                       "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf")


def _color(value: object) -> QColor:
    """A QColor from a QColor, anything QColor() understands ("#rrggbb", a name, …), or None
    for the default curve colour."""
    if value is None:
        return QColor(DEFAULT_COLOR)
    return QColor(value) if not isinstance(value, QColor) else QColor(value)


def _parse(value: str | sp.Basic) -> tuple[str, sp.Expr, bool]:
    """Canonical text, normalized expression and whether the input was an equation.

    ``Eq(lhs, rhs)`` becomes the level set ``lhs - rhs = 0`` and is flagged as implicit even
    when only ``x`` is free (``Eq(x**2, 1)`` is two vertical lines, not ``y = x**2 - 1``).
    """
    if isinstance(value, sp.Basic):
        text = str(value)
        expr = value
    else:
        text = str(value)
        expr = sp.sympify(value, locals={"x": sp.Symbol("x"), "y": sp.Symbol("y")})
    if isinstance(expr, sp.Eq):
        return text, expr.lhs - expr.rhs, True
    return text, expr, False


def parsed(value: str | sp.Basic) -> tuple[str, sp.Expr]:
    """The canonical string and the parsed expression of a curve input.

    A string is parsed here, once; a sympy expression is used as it is (no ``Expr -> str ->
    Expr`` round trip). ``Eq(lhs, rhs)`` is normalized to ``lhs - rhs``. The canonical string
    is what identifies the expression.
    """
    text, expr, _ = _parse(value)
    return text, expr


def curve_for(
    expression: str | sp.Basic,
    *,
    implicit: bool | None = None,
    color: object = None,
    line_width: object = None,
    label: str = "",
    parent: QObject | None = None,
) -> Curve:
    """Create the artist an expression needs.

    Auto-routed by the free symbols: ``x`` (or none) is a 1-D curve ``y = f(x)``, ``x`` and ``y``
    is an implicit curve (the level set ``F(x, y) = 0``), and anything else raises — a parameter
    must be substituted before it is plotted, and silently evaluating it as a pixel coordinate is
    exactly the trap this avoids. ``implicit=True`` picks the level-set artist regardless of the
    symbols (``add_implicit``). A sympy ``Eq`` is always a level set (``lhs - rhs = 0``), even
    when only ``x`` is free; it keeps the ordinary ``Curve`` class so a later assignment can
    re-route it.
    """
    text, expr, is_equation = _parse(expression)
    names = {symbol.name for symbol in expr.free_symbols}
    if names - {"x", "y"}:
        raise ValueError(
            f"{sorted(names - {'x', 'y'})} must be substituted first: only x (or x and y) may "
            f"be free, got {text!r}"
        )
    if implicit:
        cls: type[Curve] = ImplicitCurve
    elif is_equation:
        cls = Curve
    else:
        cls = ImplicitCurve if "y" in names else Curve
    source = expression if is_equation else expr
    return cls(source, color=color, line_width=line_width, label=label, parent=parent)


class Curve(QObject):
    """One curve: an expression, its baked shaders, its style and its error text.

    The fragment shader follows the expression: ``y = f(x)`` while only ``x`` (or nothing) is
    free, the level set ``F(x, y) = 0`` as soon as ``y`` appears — so ``add_curve`` and assigning
    to ``expression`` both route themselves, and a text field can turn a function into a level
    set. A sympy ``Eq(lhs, rhs)`` is always a level set (``lhs - rhs = 0``), even when only ``x``
    is free. ``ImplicitCurve`` forces the level-set template regardless of the symbols.
    """

    #: Forced to the level-set template (see ``ImplicitCurve``).
    _force_implicit = False

    #: Bake expression changes on the thread pool (the initial construction always bakes
    #: synchronously, so a curve has shaders the moment it exists). Tests turn this off.
    bake_async: bool = True

    expressionChanged = Signal()
    exprChanged = Signal()
    styleChanged = Signal()
    visibleChanged = Signal()
    labelChanged = Signal()
    errorChanged = Signal()
    shadersChanged = Signal()

    def __init__(
        self,
        expression: str | sp.Basic = "sin(x)",
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
        self._generation = 0
        self._pending_expr: sp.Expr | None = None
        self._pending_implicit: bool | None = None
        # Not parented: a job may outlive the curve; the signal object must stay valid for the
        # queued connection (Qt disconnects the receiver when the curve is destroyed).
        self._bake_signals = _BakeSignals()
        self._bake_signals.done.connect(self._on_bake_done, Qt.ConnectionType.QueuedConnection)
        self._set_expression(expression, synchronous=True)

    # ---------------------------------------------------------------- Expression
    def _get_expression(self) -> str:
        return self._expression

    def _set_expression(self, source: str | sp.Basic, *, synchronous: bool = False) -> None:
        """Set the expression from a string (parsed here, once) or from a sympy expression.

        The canonical **string** is what identifies the expression — ``sp.sin(x)`` and
        ``"sin(x)"`` are the same, and re-setting either is free — while the parsed
        ``sp.Expr`` is cached for the bake and for the matplotlib hand-off, so neither ever
        stringifies and re-parses again. ``Eq(lhs, rhs)`` is normalized to ``lhs - rhs`` and
        drawn as the level set, even when only ``x`` is free.

        Parsing and source generation are synchronous (microseconds); the qsb subprocess runs
        on the thread pool for later assignments. ``expr`` keeps the last successfully baked
        expression until the new shaders arrive, so a failed or pending edit never draws an
        incomplete curve.
        """
        canonical = source if isinstance(source, str) else str(source)
        if canonical == self._expression:
            return
        self._generation += 1
        generation = self._generation
        self._pending_expr = None
        self._pending_implicit = None
        try:
            _, expr, is_equation = _parse(source)
        except Exception as exc:
            # Keep the last drawn expression: the shaders stay, `expression` is what was
            # typed and `error` says why.
            self._expression = str(canonical)
            self._error = f"{type(exc).__name__}: {exc}"
            self.expressionChanged.emit()
            self.errorChanged.emit()
            return
        forced_implicit = type(self)._force_implicit or is_equation
        self._expression = str(canonical)
        self.expressionChanged.emit()
        self._start_bake(
            generation, expr, synchronous=synchronous, force_implicit=forced_implicit
        )

    # Annotate the value type (PySide6 idiom): Property is a descriptor, so without the
    # annotation a type checker only sees the Property object and flags every read and write.
    # Declared "QVariant" so an ``sp.Expr`` reaches the setter unconverted; the getter is always
    # the canonical string (QML, a project file and the text field all want one).
    expression: str | sp.Basic = Property("QVariant", _get_expression, _set_expression,
                                          notify=expressionChanged)

    def _get_expr(self) -> sp.Expr:
        return self._expr

    #: The parsed expression being drawn (read-only): the object form the setter accepts, and
    #: the one the bake and the matplotlib hand-off use. It follows the last expression that
    #: compiled, so `expression` can show a failing edit while `expr` keeps the drawn curve.
    expr: sp.Expr = Property("QVariant", _get_expr, notify=exprChanged)

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

    @property
    def implicit(self) -> bool:
        """Whether this curve is currently drawn as a level set ``F(x, y) = 0``.

        Read-only, and it follows the expression: assigning ``"x**2 + y**2 - 1"`` switches this
        curve's shader to the implicit one. The matplotlib hand-off uses it to skip level sets,
        which cannot be sampled into a polyline.
        """
        return self._force_implicit or "y" in {symbol.name for symbol in self._expr.free_symbols}

    def _sources(
        self, expr: sp.Expr, *, force_implicit: bool | None = None
    ) -> tuple[str, str]:
        """The shader sources for this expression; anything unrenderable raises ``ValueError``
        (which ``_start_bake`` turns into ``error``, keeping the last working shaders)."""
        names = {symbol.name for symbol in expr.free_symbols}
        if names - {"x", "y"}:
            raise ValueError(
                f"only x (or x and y) may be free, got {sorted(names)}: substitute parameters "
                "first"
            )
        implicit = self._force_implicit if force_implicit is None else force_implicit
        return implicit_shader_sources(expr) if (implicit or "y" in names) \
            else shader_sources(expr)

    def _start_bake(
        self,
        generation: int,
        expr: sp.Expr,
        *,
        synchronous: bool,
        force_implicit: bool | None = None,
    ) -> None:
        """Generate the GLSL now and bake it on the pool unless this curve is synchronous.

        Source generation is cheap and stays on the caller's thread; only the qsb subprocess
        (the ~126 ms cache miss) leaves it.
        """
        try:
            vertex_source, fragment_source = self._sources(
                expr, force_implicit=force_implicit
            )
        except Exception as exc:  # noqa: BLE001 - reported through error, previous shaders stay
            self._error = f"{type(exc).__name__}: {exc}"
            self.errorChanged.emit()
            return
        self._pending_expr = expr
        self._pending_implicit = force_implicit
        if synchronous or not self.bake_async or QCoreApplication.instance() is None:
            self._run_bake(generation, vertex_source, fragment_source)
            return
        QThreadPool.globalInstance().start(
            _BakeJob(self._bake_signals, generation, vertex_source, fragment_source)
        )

    def _run_bake(self, generation: int, vertex_source: str, fragment_source: str) -> None:
        """The synchronous path (construction, headless scripts, tests)."""
        try:
            vertex = bake(vertex_source, "vert")
            fragment = bake(fragment_source, "frag")
        except Exception as exc:  # noqa: BLE001
            self._on_bake_done(generation, exc)
        else:
            self._on_bake_done(generation, (vertex, fragment))

    def _on_bake_done(self, generation: int, result: object) -> None:
        """Apply a bake result unless a newer assignment superseded it."""
        if generation != self._generation:
            return
        expr = self._pending_expr
        implicit = self._pending_implicit
        self._pending_expr = None
        self._pending_implicit = None
        if isinstance(result, Exception):
            self._error = f"{type(result).__name__}: {result}"
            self.errorChanged.emit()
            return
        if expr is None:
            return
        vertex, fragment = result           # type: ignore[misc]
        self._expr = expr
        if implicit is not None:
            self._force_implicit = bool(implicit)
        self._error = ""
        self._vertex = QUrl.fromLocalFile(str(vertex))
        self._fragment = QUrl.fromLocalFile(str(fragment))
        self.exprChanged.emit()
        self.errorChanged.emit()
        self.shadersChanged.emit()


class ImplicitCurve(Curve):
    """A curve drawn from the level set ``F(x, y) = 0``, whatever the free symbols.

    No sampling and no envelope band: one evaluation of ``F`` and its two partials per pixel give
    the distance to the level set (``docs/api-design.md`` §6b). Thin features and several
    branches through one pixel are not handled yet, and a domain/pole curve is not special-cased
    either — pixels where ``F`` is undefined come out NaN and are dropped.
    """

    _force_implicit = True


class CurveListModel(QAbstractListModel):
    """The plot's curves, as a model the QRhi renderer and QML chrome read."""

    CurveRole = int(Qt.ItemDataRole.UserRole) + 1
    ExpressionRole = CurveRole + 1
    ColorRole = CurveRole + 2
    LineWidthRole = CurveRole + 3
    VisibleRole = CurveRole + 4
    VertexShaderRole = CurveRole + 5
    FragmentShaderRole = CurveRole + 6
    LabelRole = CurveRole + 7

    _ROLES = {
        CurveRole: b"curve",
        ExpressionRole: b"expression",
        ColorRole: b"color",
        LineWidthRole: b"lineWidth",
        VisibleRole: b"visible",
        VertexShaderRole: b"vertexShader",
        FragmentShaderRole: b"fragmentShader",
        LabelRole: b"label",
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
        if role == self.LabelRole:
            return curve.label
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
        curve.labelChanged.connect(lambda c=curve: self._notify(c, (self.LabelRole,)))

    # ---------------------------------------------------------------- public
    def _defaults(self, color: object, line_width: object) -> tuple[QColor, float]:
        if color is None:
            color = self._cycle[len(self._curves) % len(self._cycle)]
        return _color(color), (self._default_line_width if line_width is None
                               else float(line_width))  # type: ignore[arg-type]

    def _append(self, curve: Curve) -> Curve:
        row = len(self._curves)
        self.beginInsertRows(QModelIndex(), row, row)
        self._curves.append(curve)
        self.endInsertRows()
        self._watch(curve)
        return curve

    @Slot("QVariant", "QVariant", "QVariant", str, result=QObject)
    @Slot("QVariant", result=QObject)
    def add_curve(
        self,
        expression: str | sp.Basic,
        color: object = None,
        line_width: object = None,
        label: str = "",
    ) -> Curve:
        """Append the artist the expression needs: ``y = f(x)``, or the level set
        ``F(x, y) = 0`` when both symbols are free (see ``curve_for``). Colour and line width
        default to the plot's cycle and width."""
        default_color, default_width = self._defaults(color, line_width)
        return self._append(curve_for(expression, color=default_color, line_width=default_width,
                                      label=label, parent=self))

    @Slot("QVariant", "QVariant", "QVariant", str, result=QObject)
    @Slot("QVariant", result=QObject)
    def add_implicit(
        self,
        expression: str | sp.Basic,
        color: object = None,
        line_width: object = None,
        label: str = "",
    ) -> Curve:
        """Append an implicit curve (the level set ``F(x, y) = 0``) explicitly, whatever the free
        symbols: ``x`` alone is a valid level set (vertical lines)."""
        default_color, default_width = self._defaults(color, line_width)
        return self._append(curve_for(expression, implicit=True, color=default_color,
                                      line_width=default_width, label=label, parent=self))

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
