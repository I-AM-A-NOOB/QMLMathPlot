"""CPU fallback renderer: one ``QPainterPath`` per curve, no shaders and no sympy.

The item draws the calculator's scalar program (``Program.math``) through the same
``curveplotter`` port the GPU path uses for ineligible expressions, so the fallback and the
renderer agree on the geometry. A bare expression string (``"tan(x)"``) is also accepted for
the legacy prototype/tests: it is evaluated with ``math`` names only.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Property, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QVector4D
from PySide6.QtQuick import QQuickPaintedItem

from .curveplotter import PlotView, compile_source, plot_curve

__all__ = ["PathCurveItem"]

#: Samples per logical pixel for the bare-expression prototype.
_SUBSAMPLES = 4
#: Beyond this world value a sample is treated as a pole, not a line vertex.
_HUGE = 1e5

_MATH_NAMES = {name: getattr(math, name) for name in dir(math) if not name.startswith("_")}
_MATH_NAMES.update(
    {
        "Abs": abs,
        "abs": abs,
        "sign": lambda value: 0 if value == 0 else (1 if value > 0 else -1),
        "sec": lambda value: 1 / math.cos(value),
        "csc": lambda value: 1 / math.sin(value),
        "cot": lambda value: 1 / math.tan(value),
        "__builtins__": {},
    }
)


class PathCurveItem(QQuickPaintedItem):
    """Draws one curve with QPainter: ``mathSource`` (a program) or ``expression`` (bare)."""

    expressionChanged = Signal()
    mathSourceChanged = Signal()
    cameraChanged = Signal()
    colorChanged = Signal()
    lineWidthChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._expression = ""
        self._math_source = ""
        self._func = None
        self._camera = QVector4D(0.0, 0.0, 1.0, 1.0)
        self._color = QColor(31, 119, 180)
        self._line_width = 3.0
        self.setAntialiasing(True)

    # ------------------------------------------------------------- properties
    @Property(str, notify=expressionChanged)
    def expression(self) -> str:
        """Legacy bare expression (``"tan(x)"``); prefer :attr:`mathSource`."""
        return self._expression

    @expression.setter
    def expression(self, value: str) -> None:
        self._expression = str(value)
        self._func = _compile_expression(self._expression)
        self.expressionChanged.emit()
        self.update()

    @Property(str, notify=mathSourceChanged)
    def mathSource(self) -> str:
        return self._math_source

    @mathSource.setter
    def mathSource(self, value: str) -> None:
        self._math_source = str(value)
        try:
            self._func = compile_source(self._math_source) if self._math_source else None
        except (TypeError, ValueError, SyntaxError):
            self._func = None
        self.mathSourceChanged.emit()
        self.update()

    @Property(QVector4D, notify=cameraChanged)
    def camera(self) -> QVector4D:
        return self._camera

    @camera.setter
    def camera(self, value: QVector4D) -> None:
        self._camera = QVector4D(value)
        self.cameraChanged.emit()
        self.update()

    @Property(QColor, notify=colorChanged)
    def color(self) -> QColor:
        return self._color

    @color.setter
    def color(self, value: QColor) -> None:
        self._color = QColor(value)
        self.colorChanged.emit()
        self.update()

    @Property(float, notify=lineWidthChanged)
    def lineWidth(self) -> float:
        return self._line_width

    @lineWidth.setter
    def lineWidth(self, value: float) -> None:
        self._line_width = float(value)
        self.lineWidthChanged.emit()
        self.update()

    # ------------------------------------------------------------------ paint
    def _view(self) -> tuple[PlotView, float, float, float, float]:
        width = max(1.0, self.width())
        height = max(1.0, self.height())
        cx, cy, sx, sy = (self._camera.x(), self._camera.y(),
                          self._camera.z(), self._camera.w())
        view = PlotView(cx - 0.5 * width * sx, cx + 0.5 * width * sx,
                        cy - 0.5 * height * sy, cy + 0.5 * height * sy,
                        int(width), int(height))
        return view, width, height, cx, cy

    def _program_path(self) -> QPainterPath | None:
        view, width, height, _cx, _cy = self._view()
        try:
            operations = plot_curve(self._math_source, view, view.xmin, view.xmax)
        except Exception:  # noqa: BLE001 - the prototype simply draws nothing
            return None
        x_scale = width / (view.xmax - view.xmin)
        y_scale = height / (view.ymax - view.ymin)

        def to_pixels(x: float, y: float) -> tuple[float, float]:
            return (x - view.xmin) * x_scale, (view.ymax - y) * y_scale

        path = QPainterPath()
        for operation in operations:
            kind = operation[0]
            if kind == "MOVE_TO":
                path.moveTo(*to_pixels(operation[1], operation[2]))
            elif kind == "LINE_TO":
                path.lineTo(*to_pixels(operation[1], operation[2]))
        return path

    def _expression_path(self) -> QPainterPath | None:
        if self._func is None:
            return None
        _view, width, height, cx, cy = self._view()
        sx, sy = self._camera.z(), self._camera.w()
        x0 = cx - 0.5 * width * sx
        n = max(2, int(width * _SUBSAMPLES))
        path = QPainterPath()
        pen_down = False
        prev_y = None
        last_py = 0.0
        for i in range(n + 1):
            x = x0 + (i / n) * width * sx
            try:
                y = float(self._func(x))
            except (ValueError, ZeroDivisionError, OverflowError, TypeError, NameError):
                pen_down = False
                prev_y = None
                last_py = 0.0
                continue
            if not math.isfinite(y):
                pen_down = False
                prev_y = None
                continue
            py = height * (0.5 - (y - cy) / (height * sy))
            if abs(y) > _HUGE or (
                prev_y is not None
                and ((prev_y * y < 0.0 and abs(y - prev_y) > 10.0)
                     or abs(py - last_py) > height * 2.0)
            ):
                pen_down = False
                prev_y = None
                continue
            if not pen_down:
                path.moveTo(i * width / n, py)
                pen_down = True
            else:
                path.lineTo(i * width / n, py)
            prev_y = y
            last_py = py
        return path

    def paint(self, painter: QPainter) -> None:
        path = self._program_path() if self._math_source else self._expression_path()
        if path is None or path.isEmpty():
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self._color, self._line_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(path)


def _compile_expression(text: str):
    """A scalar callable for a bare expression, using ``math`` names only."""
    if not text:
        return None
    try:
        code = compile(text, "<expression>", "eval")
    except (SyntaxError, ValueError):
        return None

    def evaluate(x: float) -> float:
        return eval(code, _MATH_NAMES, {"x": x})  # noqa: S307 - our own text, math namespace

    return evaluate
