"""Prototype: draw a 1-D curve as one QPainterPath, like GeoGebra's IntervalPathPlotter.

This is intentionally separate from the production shader: it shows the path-based
geometry that removes the per-column seams on tan/log before it is wired into PlotView.
"""

from __future__ import annotations

import math

import sympy as sp
from PySide6.QtCore import Property, Qt, Signal
from PySide6.QtGui import QVector4D
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtQuick import QQuickPaintedItem

__all__ = ["PathCurveItem"]

#: Samples per logical pixel. 4 keeps a near-vertical tan branch on its own path without
#: drawing a connector across a pole.
_SUBSAMPLES = 4
#: Beyond this world value a sample is treated as a pole, not a line vertex.
_HUGE = 1e5


class PathCurveItem(QQuickPaintedItem):
    expressionChanged = Signal()
    cameraChanged = Signal()
    colorChanged = Signal()
    lineWidthChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._expression = ""
        self._func = None
        self._camera = QVector4D(0.0, 0.0, 1.0, 1.0)
        self._color = QColor(31, 119, 180)
        self._line_width = 3.0
        self.setAntialiasing(True)

    @Property(str, notify=expressionChanged)
    def expression(self) -> str:
        return self._expression

    @expression.setter
    def expression(self, value: str) -> None:
        self._expression = value
        try:
            x = sp.Symbol("x")
            self._func = sp.lambdify(x, sp.sympify(value), modules="math")
        except Exception:  # noqa: BLE001 - prototype reports an empty curve
            self._func = None
        self.expressionChanged.emit()
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

    def paint(self, painter: QPainter) -> None:
        if self._func is None:
            return
        width = max(1.0, self.width())
        height = max(1.0, self.height())
        cx, cy, sx, sy = (self._camera.x(), self._camera.y(),
                          self._camera.z(), self._camera.w())
        x0 = cx - 0.5 * width * sx
        n = max(2, int(width * _SUBSAMPLES))
        path = QPainterPath()
        pen_down = False
        prev_y = None
        self._last_py = 0.0
        for i in range(n + 1):
            x = x0 + (i / n) * width * sx
            try:
                y = float(self._func(x))
            except (ValueError, ZeroDivisionError, OverflowError):
                pen_down = False
                prev_y = None
                self._last_py = 0.0
                continue
            if not math.isfinite(y):
                pen_down = False
                prev_y = None
                continue
            py = height * (0.5 - (y - cy) / (height * sy))
            # A pole: both samples are enormous and/or the sign flipped across it. Do not
            # connect the two branches; the path resumes on the next sample.
            if abs(y) > _HUGE or (
                prev_y is not None
                and ((prev_y * y < 0.0 and abs(y - prev_y) > 10.0)
                     or abs(py - self._last_py) > height * 2.0)
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
            self._last_py = py
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self._color, self._line_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(path)
