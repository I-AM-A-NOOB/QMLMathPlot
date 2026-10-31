# SPDX-FileCopyrightText: 2007-2026 GeoGebra GmbH
# SPDX-License-Identifier: GPL-3.0-only
# Portions derived from GeoGebra (EUPL-1.2); see THIRD_PARTY_NOTICES.md.
"""GeoGebra's interval path as a state machine over column topologies.

This is the semantic core of ``org.geogebra.common.euclidian.plot.interval``:
``IntervalPath`` (break/join state), ``DrawInterval`` (joined/independent/whole),
``DrawInvertedInterval`` (gap rays) and ``JoinLines`` (neighbour-aware completion of the
inverted and infinite cases). It contains **no interval arithmetic and no sampling**: the
input is one y-topology per screen column (empty / connected / inverted / whole), the output
is screen-space segments.

Keeping the decisions here rather than inline in the GLSL gives the GPU path a host-testable
specification: synthetic topologies can exercise every branch without a GPU and without a
second interval evaluator. ``ggb_reference`` feeds it from ``mpmath.iv``; ``ggb_compute``
mirrors it in the compute shader, consuming the float32 ``INTERVAL_LIB`` topology.

Coordinates are screen pixels (y down), matching GGB's path. The y interval is stored in
world units; the conversions happen at the edges.

Both ``JoinLines`` comparisons are ported literally, because GGB's own plotter output
shows they matter: the top-edge equality is a **tolerance** (``MAX_PRECISION = 1e-12`` in
double; a float-safe relative tolerance on the GPU), and ``toBottom`` compares differences
**after** ``toScreenCoordYd``, which reverses their order. ``VERTICAL_MARGIN`` is not
reproduced; the GPU clips the segment to the viewport anyway.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

__all__ = ["IVal", "Column", "PathPoint", "IntervalPath", "IINF"]

Kind = Literal["empty", "connected", "inverted", "whole"]

#: The GLSL library's infinity sentinel (``IINF``); real infinities are avoided on the GPU.
IINF = 1e30


@dataclass(frozen=True)
class IVal:
    """A conservative y value: a connected range, a gap, the whole line, or empty."""

    lo: float
    hi: float
    kind: Kind = "connected"

    @property
    def empty(self) -> bool:
        return self.kind == "empty"

    @property
    def whole(self) -> bool:
        return self.kind == "whole"

    @property
    def inverted(self) -> bool:
        return self.kind == "inverted"

    def has_infinity(self) -> bool:
        if self.kind == "empty":
            return False
        if self.kind == "whole":
            return True
        return self.lo <= -IINF or self.hi >= IINF

    # Payload edges: an inverted value stores the excluded gap, a whole value is unbounded.
    @property
    def payload_lo(self) -> float:
        if self.kind == "whole":
            return -IINF
        return self.lo

    @property
    def payload_hi(self) -> float:
        if self.kind == "whole":
            return IINF
        return self.hi


@dataclass(frozen=True)
class Column:
    x_lo: float
    x_hi: float
    y: IVal


@dataclass
class PathPoint:
    x: float
    y: float


def _near(a: float, b: float, fraction: float = 1e-9) -> bool:
    """``DoubleUtil.isEqual(0, |a-b|, MAX_PRECISION)`` with a scale-aware epsilon.

    The double version uses 1e-12; the GPU needs a larger relative epsilon for float32.
    """
    return abs(a - b) <= fraction * max(1.0, abs(a), abs(b))


class IntervalPath:
    """Turns per-column topologies into a path, the way GGB's ``IntervalPath`` does.

    ``columns`` must be in screen-column order. The x interval of a column is its world
    span; ``width``/``height`` are the view's pixel size and the ``x_*``/``y_*`` arguments
    are its world bounds.
    """

    def __init__(
        self,
        columns: list[Column],
        width: float,
        height: float,
        x_lo: float,
        x_hi: float,
        y_lo: float,
        y_hi: float,
    ) -> None:
        self.columns = columns
        self.width = float(width)
        self.height = float(height)
        self.x_lo = float(x_lo)
        self.x_hi = float(x_hi)
        self.y_lo = float(y_lo)
        self.y_hi = float(y_hi)
        self.segments: list[tuple[PathPoint, PathPoint]] = []
        self._point: PathPoint | None = None
        self._last: IVal | None = None

    # ------------------------------------------------------------------ driver
    def update(self) -> list[tuple[PathPoint, PathPoint]]:
        """Reset and rebuild the path; returns screen-space segments."""
        del self.segments[:]
        self._point = None
        self._last = None
        for index in range(len(self.columns)):
            self._draw_at(index)
        return list(self.segments)

    def _draw_at(self, index: int) -> None:
        column = self.columns[index]
        y = column.y
        if y.empty:
            self._last = None
            return
        if self._last is None:
            self._draw_independent(index)
        elif y.whole:
            self._draw_whole(column)
        elif y.inverted:
            self._draw_inverted(index)
        elif y.has_infinity():
            self._draw_normal_infinity(column)
        else:
            self._draw_joined(column)

    # ------------------------------------------------------- DrawInterval
    def _draw_joined(self, column: Column) -> None:
        """``DrawInterval.drawUp``/``drawDown``: connect to the previous column edge."""
        assert self._last is not None
        y = column.y
        # GGB compares the *screen* high edges; screen high is the world low edge.
        up = y.lo < self._last.lo
        if up:
            first = (self._sx(column.x_lo), self._sy(y.hi))
            second = (self._sx(column.x_hi), self._sy(y.lo))
        else:
            first = (self._sx(column.x_lo), self._sy(y.lo))
            second = (self._sx(column.x_hi), self._sy(y.hi))
        self._line_to(*first)
        self._line_to(*second)
        self._last = y

    def _draw_independent(self, index: int) -> None:
        """``IntervalPath.drawTupleIndependent``."""
        column = self.columns[index]
        y = column.y
        if y.whole:
            self._draw_whole(column)
            return
        if y.inverted:
            self._draw_inverted(index)
            return
        # DrawInterval.drawIndependent -> line(x, y): a vertical at the right edge. Infinities
        # are clamped by ``_sy``; the segment runs off screen and is clipped.
        self._segment(
            (self._sx(column.x_hi), self._sy(y.hi)),
            (self._sx(column.x_hi), self._sy(y.lo)),
        )
        self._last = y

    def _draw_whole(self, column: Column) -> None:
        """``DrawInterval.drawWhole``: one vertical at x.low, then a path break."""
        x = self._sx(column.x_lo)
        self._segment((x, 0.0), (x, self.height))
        self._last = None

    # ------------------------------------------------ DrawNormalInfinity
    def _draw_normal_infinity(self, column: Column) -> None:
        """``IntervalPath.drawNormalInfinity`` / ``IntervalPathPlotter.leftToTop``."""
        y = column.y
        if y.lo <= -IINF and y.hi >= IINF:
            self._last = None
            return
        if y.hi >= IINF:
            if self.y_lo <= y.lo <= self.y_hi:
                self._segment(
                    (self._sx(column.x_lo), self._sy(y.lo)),
                    (self._sx((column.x_lo + column.x_hi) * 0.5), 0.0),
                )
                # ``lastY = zero()`` in screen space: the path continues from the top edge.
                self._last = IVal(self.y_hi, self.y_hi, "connected")
            else:
                self._last = None
            return
        if self.y_lo <= y.hi <= self.y_hi:
            self._segment(
                (self._sx(column.x_lo), self._sy(y.hi)),
                (self._sx((column.x_lo + column.x_hi) * 0.5), self.height),
            )
            self._last = IVal(self.y_lo, self.y_lo, "connected")
        else:
            self._last = None

    # ------------------------------------- DrawInvertedInterval / JoinLines
    def _draw_inverted(self, index: int) -> None:
        """``DrawInvertedInterval.draw`` for both the joined and independent cases.

        ``drawJoined`` does not use ``lastY`` for the inverted branch; it only resets it
        when the current topology is whole, so the two entry points coincide.
        """
        column = self.columns[index]
        right = self.columns[index + 1] if index + 1 < len(self.columns) else None
        if right is not None and right.y.inverted:
            self._inverted_segments(column)
        else:
            self._inverted_joined(index)
            # DrawInvertedInterval.draw() clears lastYSet when the next column is not
            # inverted, so the following connected column restarts independently. Without
            # this the stale lastY joins the two branches across the pole.
            self._last = None

    def _inverted_segments(self, column: Column) -> None:
        """``DrawInvertedInterval.drawSegments``: the gap is excluded, so two rays."""
        gap = column.y
        if gap.hi > self.y_lo:
            self._segment(
                (self._sx(column.x_lo), self.height),      # world y_min
                (self._sx(column.x_hi), self._sy(gap.lo)),
            )
        if gap.lo < self.y_hi:
            self._segment(
                (self._sx(column.x_hi), 0.0),              # world y_max
                (self._sx(column.x_hi), self._sy(gap.hi)),
            )

    def _inverted_joined(self, index: int) -> None:
        """``JoinLines.inverted`` with the current/left/right tuples."""
        column = self.columns[index]
        gap = column.y
        left = self.columns[index - 1] if index > 0 else None
        right = self.columns[index + 1] if index + 1 < len(self.columns) else None
        left_y = left.y if left is not None else None
        right_y = right.y if right is not None else None

        if gap.hi < IINF:
            if not self._infinite_boundary(left_y):
                self._to_top(column, left, right)
        if gap.lo > -IINF:
            if self._infinite_boundary(right_y):
                y1 = left_y.lo if left_y is not None and not left_y.empty else gap.lo
                x = self._sx(column.x_lo)
                self._segment((x, self._sy(y1)), (x, self.height))
            else:
                self._to_bottom(column, left, right)

    @staticmethod
    def _infinite_boundary(y: IVal | None) -> bool:
        return y is not None and not y.empty and y.has_infinity()

    def _to_top(self, column: Column, left: Column | None, right: Column | None) -> None:
        current = column.y.payload_hi
        left_diff = abs(current - left.y.payload_hi) if left is not None else float("inf")
        right_diff = abs(current - right.y.payload_hi) if right is not None else float("inf")
        # GGB reads ``left/rightXHigh`` without a null check; at a view edge one neighbour is
        # missing, so fall back to this column's own edges (the segment is clipped anyway).
        left_x = self._sx(left.x_hi) if left is not None else self._sx(column.x_lo)
        right_x = self._sx(right.x_hi) if right is not None else self._sx(column.x_hi)
        x_low = self._sx(column.x_lo)
        x_high = self._sx(column.x_hi)
        if left is not None and _near(left_diff, 0.0):
            self._segment((x_low, self._sy(left.y.payload_hi)), (x_low, 0.0))
        elif right is not None and _near(right_diff, 0.0):
            self._segment((x_high, self._sy(right.y.payload_hi)), (x_high, 0.0))
        elif left_diff < right_diff:
            self._segment((right_x, 0.0), (left_x, self._sy(current)))
        else:
            self._segment((right_x, 0.0), (right_x, self._sy(current)))

    def _screen_difference(self, value: float, neighbour: Column | None) -> float:
        """``JoinLines.getLeftScreenDifference``: the difference in *screen* space.

        ``toScreenCoordYd`` is decreasing, so this reverses the ordering of a world-space
        comparison. GGB's own tan output depends on it; using raw world differences selected
        the mirrored branch and drew the asymptote on the wrong side.
        """
        if neighbour is None or neighbour.y.empty:
            return -1.0
        if neighbour.y.whole:
            return -math.inf
        return self._sy(value)

    def _to_bottom(self, column: Column, left: Column | None, right: Column | None) -> None:
        current = column.y.payload_lo
        left_diff = (self._screen_difference(abs(current - left.y.payload_lo), left)
                     if left is not None else -1.0)
        right_diff = (self._screen_difference(abs(current - right.y.payload_lo), right)
                      if right is not None else -1.0)
        right_x = self._sx(right.x_hi) if right is not None else self._sx(column.x_hi)
        x_low = self._sx(column.x_lo)
        x_high = self._sx(column.x_hi)
        if left_diff == 0.0 and left is not None:
            self._segment((x_low, self._sy(left.y.payload_lo)), (x_low, self.height))
        elif right_diff == 0.0 and right is not None:
            self._segment((x_high, self._sy(right.y.payload_lo)), (x_high, self.height))
        elif left_diff < right_diff:
            # JoinLines.toBottomLeft: to *currentXHigh*, not currentXLow.
            self._segment((right_x, self._sy(current)), (x_high, self.height))
        else:
            self._segment((x_low, self._sy(current)), (x_high, self.height))

    # ------------------------------------------------------------- primitives
    def _line_to(self, x: float, y: float) -> None:
        if self._point is None:
            self._point = PathPoint(x, y)
            return
        self.segments.append((self._point, PathPoint(x, y)))
        self._point = PathPoint(x, y)

    def _segment(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        """``IntervalPathPlotter.segment``: starts a new subpath."""
        self.segments.append((PathPoint(*start), PathPoint(*end)))
        self._point = PathPoint(*end)

    def _sx(self, x: float) -> float:
        return (x - self.x_lo) / (self.x_hi - self.x_lo) * self.width

    def _sy(self, y: float) -> float:
        # Clamp before mapping: the sentinel infinities would otherwise produce inf/NaN.
        y = max(-1e12, min(1e12, y))
        return (self.y_hi - y) / (self.y_hi - self.y_lo) * self.height
