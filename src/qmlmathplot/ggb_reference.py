# SPDX-FileCopyrightText: 2007-2026 GeoGebra GmbH
# SPDX-License-Identifier: GPL-3.0-only
# Portions derived from GeoGebra (EUPL-1.2); see THIRD_PARTY_NOTICES.md.
"""CPU sampler for GeoGebra's interval path, using the shared state machine.

``IntervalSampler`` produces one conservative y interval per screen column with
``mpmath.iv`` (the CPU analogue of the GPU's ``INTERVAL_LIB``). The path decisions live in
:mod:`qmlmathplot.ggb_path`, which is shared with the tests and mirrored by the compute
shader; this module adds no geometry of its own.

``mpmath.iv`` cannot express a gap: an interval through a pole (``tan``, ``1/x``) comes back
as ``[-inf, +inf]``, which GGB's ``IntervalSet`` would call *inverted* with a finite gap.
Such a column is reported as **empty** here — a path break — because drawing it as a whole
column would bridge the pole. The GPU's ``FI`` does produce the inverted topology, and the
state machine handles it; the synthetic tests in ``tests/test_ggb_path.py`` cover that
branch.
"""

from __future__ import annotations

import mpmath as mp
import sympy as sp

from .ggb_path import Column, IINF, IntervalPath, IVal, PathPoint

__all__ = ["Column", "PathPoint", "IVal", "IntervalSampler", "IntervalPathReference"]


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
            return IVal(0.0, 0.0, "empty")
        if not (a == a and b == b) or a > b:
            return IVal(0.0, 0.0, "empty")
        if a <= -IINF and b >= IINF:
            # A pole: the true image is an inverted gap, which mpmath.iv cannot express.
            return IVal(0.0, 0.0, "empty")
        return IVal(max(a, -IINF), min(b, IINF), "connected")


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


#: Backwards-compatible name: the CPU backend and its tests import this.
IntervalPathReference = IntervalPath
