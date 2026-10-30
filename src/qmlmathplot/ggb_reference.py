"""CPU reference port of GeoGebra's interval path, used as an oracle for the GPU port.

This is deliberately not wired into the renderer. It ports the parts of
``org.geogebra.common.euclidian.plot.interval`` and
``org.geogebra.common.kernel.interval`` that decide the path:

* one interval per screen column (``FunctionSampler`` / ``DiscreteSpaceCentered``);
* ``IntervalPath`` with ``lastYSet``, break and join state;
* ``DrawInterval.drawJoined`` / ``drawIndependent``;
* ``DrawInvertedInterval`` and ``JoinLines``.

Interval evaluation itself uses ``mpmath.iv`` as a conservative oracle rather than a second
hand-written interval arithmetic library. The production interval evaluator is the GLSL
``INTERVAL_LIB``; this module is for behavioural comparisons.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import mpmath as mp
import sympy as sp

__all__ = ["Column", "PathPoint", "IntervalSampler", "IntervalPathReference"]

Kind = Literal["empty", "connected", "inverted", "whole"]


@dataclass(frozen=True)
class IVal:
    lo: float
    hi: float
    kind: Kind

    @property
    def empty(self) -> bool:
        return self.kind == "empty"

    @property
    def whole(self) -> bool:
        return self.kind == "whole"


@dataclass(frozen=True)
class Column:
    x_lo: float
    x_hi: float
    y: IVal


@dataclass
class PathPoint:
    x: float
    y: float


class IntervalSampler:
    """One conservative y interval per screen column (GGB FunctionSampler shape)."""

    def __init__(self, expression: str | sp.Basic, x_lo: float, x_hi: float,
                 width: int) -> None:
        x = sp.Symbol("x")
        self._expr = expression if isinstance(expression, sp.Basic) else sp.sympify(expression)
        self._func = sp.lambdify(x, self._expr, modules=[_iv_module()])
        self._x_lo = x_lo
        self._x_hi = x_hi
        self._width = max(1, int(width))

    def columns(self) -> list[Column]:
        dx = (self._x_hi - self._x_lo) / self._width
        out: list[Column] = []
        for i in range(self._width):
            lo = self._x_lo + i * dx
            hi = lo + dx
            out.append(Column(lo, hi, self._y_interval(lo, hi)))
        return out

    def _y_interval(self, lo: float, hi: float) -> IVal:
        try:
            value = self._func(mp.iv.mpf([lo, hi]))
            a, b = float(value.a), float(value.b)
        except (ValueError, ZeroDivisionError, OverflowError):
            return IVal(1.0, -1.0, "empty")
        if not (a == a and b == b) or a > b:
            return IVal(1.0, -1.0, "empty")
        if a <= -1e30 and b >= 1e30:
            return IVal(-1e30, 1e30, "whole")
        return IVal(max(a, -1e30), min(b, 1e30), "connected")


def _iv_module() -> dict[str, object]:
    return {
        "sin": mp.iv.sin,
        "cos": mp.iv.cos,
        "tan": mp.iv.tan,
        "sqrt": mp.iv.sqrt,
        "exp": mp.iv.exp,
        "log": mp.iv.log,
        "Abs": abs,
    }


class _Neighbours:
    def __init__(self, current: Column, left: Column | None, right: Column | None,
                 left_topology: IVal | None, right_topology: IVal | None) -> None:
        self.current = current
        self.left = left
        self.right = right
        self.left_topology = left_topology
        self.right_topology = right_topology


class IntervalPathReference:
    """A literal-ish port of GGB's IntervalPath + DrawInterval + JoinLines."""

    def __init__(self, columns: list[Column], width: float, height: float,
                 x_lo: float, x_hi: float, y_lo: float, y_hi: float) -> None:
        self.columns = columns
        self.width = width
        self.height = height
        self.x_lo = x_lo
        self.x_hi = x_hi
        self.y_lo = y_lo
        self.y_hi = y_hi
        self.segments: list[tuple[PathPoint, PathPoint]] = []
        self._last_point: PathPoint | None = None
        self._last_y: IVal | None = None

    def update(self) -> list[tuple[PathPoint, PathPoint]]:
        self.segments.clear()
        self._last_point = None
        self._last_y = None
        for i, column in enumerate(self.columns):
            y = column.y
            if y.empty or y.whole:
                self._last_point = None
                self._last_y = None
                continue
            left = self.columns[i - 1] if i > 0 else None
            right = self.columns[i + 1] if i + 1 < len(self.columns) else None
            self._draw_column(column, left, right)
            self._last_y = y
        return list(self.segments)

    # ---- GGB DrawInterval -----------------------------------------------------------
    def _draw_column(self, column: Column, left: Column | None, right: Column | None) -> None:
        y = column.y
        x0 = self._sx(column.x_lo)
        x1 = self._sx(column.x_hi)
        if y.kind == "inverted":
            self._draw_inverted(column, left, right)
            return
        # drawJoined: compare the current high edge with lastY's high edge.
        if self._last_y is not None and y.hi > self._last_y.hi:
            self._line(x0, self._sy(y.lo), x1, self._sy(y.hi))
        else:
            self._line(x0, self._sy(y.hi), x1, self._sy(y.lo))
        self._last_y = y

    def _draw_inverted(self, column: Column, left: Column | None, right: Column | None) -> None:
        neighbours = _Neighbours(column, left, right,
                                 left.y if left else None,
                                 right.y if right else None)
        if right is None or right.y.kind != "inverted":
            self._join_inverted(neighbours)
        else:
            self._segments(column)

    def _segments(self, column: Column) -> None:
        x0 = self._sx(column.x_lo)
        x1 = self._sx(column.x_hi)
        gap = column.y
        if gap.lo < self.y_hi:
            self._line(x1, 0.0, x1, self._sy(gap.hi))
        if gap.hi > self.y_lo:
            self._line(x0, self.height, x1, self._sy(gap.lo))

    # ---- GGB JoinLines (inverted branch) --------------------------------------------
    def _join_inverted(self, n: _Neighbours) -> None:
        gap = n.current.y
        if gap.hi < 1e30:
            if n.left_topology is None or n.left_topology.whole:
                self._to_top(n)
        if gap.lo > -1e30:
            if n.right_topology is not None and n.right_topology.whole:
                self._line(self._sx(n.current.x_lo), self.height,
                           self._sx(n.current.x_lo), 0.0)
            else:
                self._to_bottom(n)

    def _to_top(self, n: _Neighbours) -> None:
        y = self._sy(n.current.y.hi)
        left_diff = (abs(n.current.y.hi - n.left.y.hi)
                     if n.left is not None else float("inf"))
        right_diff = (abs(n.current.y.hi - n.right.y.hi)
                      if n.right is not None else float("inf"))
        x_low = self._sx(n.current.x_lo)
        x_high = self._sx(n.current.x_hi)
        if left_diff == 0.0:
            if n.left is not None:
                self._line(x_low, self._sy(n.left.y.hi), x_low, 0.0)
        elif right_diff == 0.0:
            if n.right is not None:
                self._line(x_high, self._sy(n.right.y.hi), x_high, 0.0)
        elif left_diff < right_diff:
            if n.left is not None and n.right is not None:
                self._line(self._sx(n.right.x_hi), 0.0,
                           self._sx(n.left.x_hi), y)
        elif n.right is not None:
            self._line(self._sx(n.right.x_hi), 0.0,
                       self._sx(n.right.x_hi), y)

    def _to_bottom(self, n: _Neighbours) -> None:
        y = self._sy(n.current.y.lo)
        left_diff = (abs(n.current.y.lo - n.left.y.lo)
                     if n.left is not None else -1.0)
        right_diff = (abs(n.current.y.lo - n.right.y.lo)
                      if n.right is not None else -1.0)
        x_low = self._sx(n.current.x_lo)
        x_high = self._sx(n.current.x_hi)
        if left_diff == 0.0:
            self._line(x_low, self._sy(n.left.y.lo), x_low, self.height)
        elif right_diff == 0.0:
            self._line(x_high, self._sy(n.right.y.lo), x_high, self.height)
        elif left_diff < right_diff:
            if n.right is not None:
                self._line(self._sx(n.right.x_hi), y,
                           x_low, self.height)
        else:
            self._line(x_low, y, x_high, self.height)

    # ---- primitive path -------------------------------------------------------------
    def _line(self, x0: float, y0: float, x1: float, y1: float) -> None:
        start = PathPoint(float(x0), float(y0))
        if self._last_point is not None:
            self.segments.append((self._last_point, start))
        self.segments.append((start, PathPoint(float(x1), float(y1))))
        self._last_point = PathPoint(float(x1), float(y1))

    def _sx(self, x: float) -> float:
        return (x - self.x_lo) / (self.x_hi - self.x_lo) * self.width

    def _sy(self, y: float) -> float:
        return (self.y_hi - y) / (self.y_hi - self.y_lo) * self.height
