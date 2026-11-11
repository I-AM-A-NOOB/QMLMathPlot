"""Curve layer: one expression -> GLSL -> .qsb plus its style, and the list model QML repeats.

A curve knows nothing about the view: the camera decides where we look, the curve decides what is
drawn. One curve = one fragment shader pass, so ``visible`` is the lever that costs nothing.

Two artists share the class: ``Curve`` draws ``y = f(x)`` (interval compute, or the CPU
CurvePlotter port for expressions the interval gate rejects) and
``ImplicitCurve`` draws the level set ``F(x, y) = 0`` (``shaders/implicit.frag``).
They differ only in the shader *sources* — the uniform block, the model roles and the QML side
are the same — so ``curve_for()`` can pick one from the expression's free symbols.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QCoreApplication,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor

from .protocol import Program, ProgramSource, ProgramSourceMissingError
from .qsb import bake

if TYPE_CHECKING:  # sympy is a calculator concern; only annotations and the legacy view need it
    import sympy as sp

__all__ = ["Curve", "CurveListModel", "ImplicitCurve", "curve_for", "parsed"]


#: Program stage name -> the qsb stage suffix (qsb decides the stage from the suffix).
_BAKE_STAGES = {"vertex": "vert", "fragment": "frag", "compute": "comp"}


class _BakeSignals(QObject):
    """The result channel of one background bake (a ``QRunnable`` cannot own signals)."""

    done = Signal(int, object)      # generation, {stage: path} or an exception


class _BakeJob(QRunnable):
    """``GLSL sources -> qsb`` off the GUI thread (qsb is a subprocess)."""

    def __init__(self, signals: _BakeSignals, generation: int, sources: dict[str, str]) -> None:
        super().__init__()
        self._signals = signals
        self._generation = generation
        self._sources = dict(sources)

    def run(self) -> None:  # noqa: D401 - QRunnable API
        try:
            baked = {stage: bake(source, _BAKE_STAGES[stage])
                     for stage, source in self._sources.items()}
        except Exception as exc:  # noqa: BLE001 - reported to the curve, never swallowed
            self._signals.done.emit(self._generation, exc)
        else:
            self._signals.done.emit(self._generation, baked)


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


def parsed(value: str | sp.Basic) -> tuple[str, sp.Expr]:
    """Legacy helper: canonical text and sympy expression (imports sympy on demand).

    ``Eq(lhs, rhs)`` is normalised to ``lhs - rhs``. The model itself no longer parses text;
    this remains for the compatibility layer and for tests that compare against sympy.
    """
    import sympy as sp

    if isinstance(value, sp.Basic):
        text = str(value)
        expr = value
    else:
        text = str(value)
        expr = sp.sympify(value, locals={"x": sp.Symbol("x"), "y": sp.Symbol("y")})
    if isinstance(expr, sp.Eq):
        return text, expr.lhs - expr.rhs
    return text, expr


def _url(path: object) -> QUrl:
    return QUrl() if path is None else QUrl.fromLocalFile(str(path))


def point_series(points: object) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Normalise ``(x_values, y_values)`` (sequences, numpy arrays, …) to float tuples."""
    try:
        xs, ys = points  # type: ignore[misc]
    except (TypeError, ValueError):
        raise ValueError("points must be an (x, y) pair of sequences") from None
    if isinstance(xs, (str, bytes)) or isinstance(ys, (str, bytes)):
        raise ValueError("points must be two numeric sequences")
    try:
        xs_t = tuple(float(value) for value in xs)  # type: ignore[union-attr]
        ys_t = tuple(float(value) for value in ys)  # type: ignore[union-attr]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"points must be numeric: {exc}") from None
    if not xs_t or len(xs_t) != len(ys_t):
        raise ValueError("x and y must have the same non-zero length")
    return xs_t, ys_t


def points_operations(
    points: tuple[tuple[float, ...], tuple[float, ...]],
) -> list[tuple[object, ...]]:
    """Data points as path operations; a non-finite x or y breaks the line."""
    import math

    xs, ys = points
    operations: list[tuple[object, ...]] = []
    down = False
    for x, y in zip(xs, ys):
        if not (math.isfinite(x) and math.isfinite(y)):
            down = False
            continue
        operations.append(("MOVE_TO" if not down else "LINE_TO", x, y))
        down = True
    return operations


def curve_for(
    expression: str | sp.Basic,
    *,
    implicit: bool | None = None,
    color: object = None,
    line_width: object = None,
    label: str = "",
    parent: QObject | None = None,
    program_source: ProgramSource | None = None,
    points: object = None,
) -> Curve:
    """Create the artist for an expression (or for data points).

    Routing happens in the calculator, not here: the curve follows the ``Program`` it receives
    (function, interval path or level set). ``implicit=True`` (``add_implicit``) forces the
    level-set artist, so a later assignment to ``expression`` keeps drawing a level set.
    """
    cls: type[Curve] = ImplicitCurve if implicit else Curve
    return cls(
        expression,
        color=color,            # type: ignore[arg-type]
        line_width=line_width,  # type: ignore[arg-type]
        label=label,
        parent=parent,
        program_source=program_source,
        points=points,
    )


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
        program_source: ProgramSource | None = None,
        points: object = None,
    ) -> None:
        super().__init__(parent)
        self._program_source = program_source
        self._expression = ""
        self._last_valid_text = ""
        self._expr_cache: tuple[str, sp.Expr] | None = None
        self._program: Program | None = None
        self._pending_program: Program | None = None
        self._color = _color(color)
        self._line_width = 1.5 if line_width is None else float(line_width)
        self._label = str(label)
        self._visible = True
        self._error = ""
        self._vertex = QUrl()
        self._fragment = QUrl()
        self._compute = QUrl()
        self._generation = 0
        self._points: tuple[tuple[float, ...], tuple[float, ...]] | None = None
        # Not parented: a job may outlive the curve; the signal object must stay valid for the
        # queued connection (Qt disconnects the receiver when the curve is destroyed).
        self._bake_signals = _BakeSignals()
        self._bake_signals.done.connect(self._on_bake_done, Qt.ConnectionType.QueuedConnection)
        if points is not None:
            self._points = point_series(points)
            self._program = Program(text="", kind="data")
        else:
            self._set_expression(expression)

    # ---------------------------------------------------------------- Expression
    def _get_expression(self) -> str:
        return self._expression

    def _set_expression(self, source: str | sp.Basic) -> None:
        """Resolve ``source`` through the configured :class:`ProgramSource`, synchronously."""
        text = source if isinstance(source, str) else str(source)
        if self._points is not None:
            self._points = None
        if not text and self._program is None:
            self._expression = ""
            return
        if text == self._expression and self._program is not None and self._program.kind != "data":
            return
        self._generation += 1
        self._expression = text
        self.expressionChanged.emit()
        self._request(text)

    def _request(self, text: str) -> None:
        source = self._program_source
        if source is None:
            raise ProgramSourceMissingError(
                "no ProgramSource configured: construct Plot(program_source=...) or call "
                "add_program() — see qmlmathplot.calc.SymPyProgramSource"
            )
        try:
            program = source.program(text, implicit=self._force_implicit or None)
        except Exception as exc:  # noqa: BLE001 - reported on the curve, never raised to QML
            self._fail(f"{type(exc).__name__}: {exc}")
            return
        self._apply_program(program)

    # Annotate the value type (PySide6 idiom): Property is a descriptor, so without the
    # annotation a type checker only sees the Property object and flags every read and write.
    # Declared "QVariant" so an ``sp.Expr`` reaches the setter unconverted; the getter is always
    # the canonical string (QML, a project file and the text field all want one).
    expression: str | sp.Basic = Property("QVariant", _get_expression, _set_expression,
                                          notify=expressionChanged)


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

    #: Empty when the compute renderer draws this expression: the RHI generates its path
    #: from `expr`. Only the sampled fallback and implicit curves bake these shaders.
    vertexShader: QUrl = Property(QUrl, _get_vertex_shader, notify=shadersChanged)
    fragmentShader: QUrl = Property(QUrl, _get_fragment_shader, notify=shadersChanged)
    def _get_compute_shader(self) -> QUrl:
        return self._compute

    #: The baked interval-path compute shader; empty for every other kind.
    computeShader: QUrl = Property(QUrl, _get_compute_shader, notify=shadersChanged)

    @property
    def is_data(self) -> bool:
        """True for a curve built from ``(x, y)`` point data rather than an expression."""
        return self._points is not None

    @property
    def points(self) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
        return self._points

    @property
    def program(self) -> Program | None:
        """The program being drawn (``None`` before the first successful one)."""
        return self._program

    @property
    def mathSource(self) -> str:
        """The program's scalar Python source (empty until a program arrives)."""
        if self._program is None or self._program.math is None:
            return ""
        return self._program.math

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
        if self._points is not None:
            return False
        if self._program is not None:
            return self._program.implicit
        return self._force_implicit

    def set_program_source(self, source: ProgramSource | None) -> None:
        """Use another source and re-resolve the current expression."""
        self._program_source = source
        if self._points is None and self._expression:
            self._generation += 1
            self._request(self._expression)

    def set_program(self, program: Program) -> None:
        """Apply a program built elsewhere: no parsing, no SymPy, no process policy."""
        if not isinstance(program, Program):
            raise TypeError(f"set_program() takes a Program, got {type(program).__name__}")
        self._points = None
        self._generation += 1
        if program.text and program.text != self._expression:
            self._expression = program.text
            self.expressionChanged.emit()
        self._apply_program(program)

    def _fail(self, message: str) -> None:
        self._error = str(message)
        self.errorChanged.emit()

    def _apply_program(self, program: Program) -> None:
        """Adopt a program and schedule its shaders (never on the GUI thread)."""
        if program.kind == "data":
            self._program = program
            self._error = ""
            self._finish_apply()
            return
        if program.error:
            self._fail(program.error)
            return
        self._pending_program = program
        self._error = ""
        sources = dict(program.glsl)
        if not sources:
            self._vertex = self._fragment = self._compute = QUrl()
            self._finish_apply()
            return
        generation = self._generation
        if not self.bake_async or QCoreApplication.instance() is None:
            self._run_bake(generation, sources)          # scripts/headless have no UI thread
        else:
            QThreadPool.globalInstance().start(
                _BakeJob(self._bake_signals, generation, sources)
            )

    def _finish_apply(self) -> None:
        # The drawn program follows a *successful* apply; a failed bake keeps the old one.
        if self._pending_program is not None:
            self._program = self._pending_program
            self._pending_program = None
        self._last_valid_text = self._expression
        self.exprChanged.emit()
        self.errorChanged.emit()
        self.shadersChanged.emit()

    def _run_bake(self, generation: int, sources: dict[str, str]) -> None:
        """The synchronous path (construction, headless scripts, tests)."""
        try:
            baked = {stage: bake(source, _BAKE_STAGES[stage])
                     for stage, source in sources.items()}
        except Exception as exc:  # noqa: BLE001
            self._on_bake_done(generation, exc)
        else:
            self._on_bake_done(generation, baked)

    def _on_bake_done(self, generation: int, result: object) -> None:
        """Apply a bake result unless a newer assignment superseded it."""
        if generation != self._generation:
            return
        if isinstance(result, Exception):
            self._pending_program = None
            self._error = f"{type(result).__name__}: {result}"
            self.errorChanged.emit()
            return
        baked = result if isinstance(result, dict) else {}
        self._vertex = _url(baked.get("vertex"))
        self._fragment = _url(baked.get("fragment"))
        self._compute = _url(baked.get("compute"))
        self._finish_apply()


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

    #: Any curve's ``error`` changed (``Plot.plotError`` aggregates these).
    curveErrorChanged = Signal()

    CurveRole = int(Qt.ItemDataRole.UserRole) + 1
    ExpressionRole = CurveRole + 1
    ColorRole = CurveRole + 2
    LineWidthRole = CurveRole + 3
    VisibleRole = CurveRole + 4
    VertexShaderRole = CurveRole + 5
    FragmentShaderRole = CurveRole + 6
    LabelRole = CurveRole + 7
    MathSourceRole = CurveRole + 8
    ImplicitRole = CurveRole + 9

    _ROLES = {
        CurveRole: b"curve",
        ExpressionRole: b"expression",
        ColorRole: b"color",
        LineWidthRole: b"lineWidth",
        VisibleRole: b"visible",
        VertexShaderRole: b"vertexShader",
        FragmentShaderRole: b"fragmentShader",
        LabelRole: b"label",
        MathSourceRole: b"mathSource",
        ImplicitRole: b"implicit",
    }

    def __init__(
        self,
        color_cycle: tuple[str, ...] = DEFAULT_COLOR_CYCLE,
        default_line_width: float = 1.5,
        parent: QObject | None = None,
        program_source: ProgramSource | None = None,
    ) -> None:
        super().__init__(parent)
        self._curves: list[Curve] = []
        self._cycle = [str(entry) for entry in color_cycle] or [DEFAULT_COLOR]
        self._default_line_width = float(default_line_width)
        self._program_source = program_source

    def set_program_source(self, source: ProgramSource | None) -> None:
        """Switch every curve (existing and future) to another text->Program source."""
        self._program_source = source
        for curve in self._curves:
            curve.set_program_source(source)

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
        if role == self.MathSourceRole:
            return curve.mathSource
        if role == self.ImplicitRole:
            return curve.implicit
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
        curve.exprChanged.connect(
            lambda c=curve: self._notify(c, (self.MathSourceRole, self.ImplicitRole))
        )
        curve.errorChanged.connect(self.curveErrorChanged)

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
                                      label=label, parent=self,
                                      program_source=self._program_source))

    @Slot("QVariant", "QVariant", "QVariant", "QVariant", str, result=QObject)
    @Slot("QVariant", "QVariant", result=QObject)
    def add_points(
        self,
        x: object,
        y: object,
        color: object = None,
        line_width: object = None,
        label: str = "",
    ) -> Curve:
        """Append a data curve: the polyline through ``(x, y)`` (NaN/inf break the line)."""
        default_color, default_width = self._defaults(color, line_width)
        return self._append(curve_for(
            "", color=default_color, line_width=default_width, label=label,
            parent=self, program_source=self._program_source, points=(x, y),
        ))

    def add_program(self, program: Program, *, color: object = None,
                    line_width: object = None, label: str = "") -> Curve:
        """Append a curve from an already-built program (no parsing, no source needed)."""
        default_color, default_width = self._defaults(color, line_width)
        curve = curve_for("", color=default_color, line_width=default_width, label=label,
                          parent=self, program_source=self._program_source)
        curve.set_program(program)
        return self._append(curve)

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
                                      line_width=default_width, label=label, parent=self,
                                      program_source=self._program_source))

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

    def __iter__(self):
        return iter(self._curves)
