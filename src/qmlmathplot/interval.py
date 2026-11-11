# SPDX-FileCopyrightText: 2007-2026 GeoGebra GmbH
# SPDX-License-Identifier: GPL-3.0-only
# Portions derived from GeoGebra (EUPL-1.2); see THIRD_PARTY_NOTICES.md.
"""Interval arithmetic for the 1-D renderer (the GeoGebra column idea, on the GPU).

GeoGebra samples one *interval* of x per pixel column and evaluates the whole expression in
interval arithmetic. The result is a conservative y-enclosure with a topology: empty,
connected, inverted (two rays around a gap), or whole. This module generates the same
evaluation as GLSL from a sympy expression, so the renderer evaluates one interval per
screen column in the compute shader.

The GLSL side is deliberately small: a value is ``(lo, hi, kind)``; an inverted set stores the
*excluded* gap; sentinel ``IINF`` stands for infinity (real infinities are not needed and make
NaN handling worse). Anything this module cannot evaluate raises :class:`Unsupported`, and the
caller uses the CPU CurvePlotter port instead.

The interval library is a port of the relevant parts of GeoGebra's
``org.geogebra.common.kernel.interval`` (``IntervalSet`` topology, ``IntervalSinCos``,
``IntervalDivide``, ``IntervalRoot``, ``IntervalMiscOperands``) reduced to what a fragment
shader needs. It is conservative: dependency overestimation may widen a band, never shrink it.
"""

from __future__ import annotations

import math
import re
from fractions import Fraction
from typing import cast

import sympy as sp

from .shaderbuild import shader_source

__all__ = [
    "INTERVAL_LIB",
    "Unsupported",
    "as_rational",
    "compute_supported",
    "interval_lib_for",
    "interval_glsl",
]


class Unsupported(Exception):
    """The expression has an operation the interval evaluator does not implement."""


# --------------------------------------------------------------------------------------------
# GLSL library
# --------------------------------------------------------------------------------------------
INTERVAL_LIB = shader_source("interval_lib.glsl")


# --------------------------------------------------------------------------------------------
# sympy -> GLSL emission
# --------------------------------------------------------------------------------------------
def _as_rational(value: sp.Expr) -> sp.Expr | None:
    """The exact rational a numeric exponent stands for, if it is close enough."""
    if value.is_Rational:
        return value
    if not value.is_number:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or abs(number) == float("inf"):
        return None
    fraction = Fraction(number).limit_denominator(1000)
    if abs(float(fraction) - number) <= 1e-12 * max(1.0, abs(number)):
        return sp.Rational(int(fraction.numerator), int(fraction.denominator))
    return None


def as_rational(value: sp.Expr) -> sp.Expr | None:
    """Public view of :func:`_as_rational` for the GLSL scalar printer/domain analysis."""
    return _as_rational(value)


def _arg(expr: sp.Expr, index: int = 0) -> sp.Expr:
    return cast(sp.Expr, expr.args[index])


def _glsl_float(value: float) -> str:
    text = f"{float(value):.9g}"
    if "." not in text and "e" not in text and "E" not in text:
        text += ".0"
    return text




class _Emitter:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.count: int = 0

    def _new(self, expression: str) -> str:
        name = f"v{self.count}"
        self.count += 1
        self.lines.append(f"    IVal {name} = {expression};")
        return name

    def emit(self, expr: sp.Expr) -> str:
        if self.count > 300:
            raise Unsupported("expression too large for the interval evaluator")
        if isinstance(expr, sp.Symbol):
            if expr.name != "x":
                raise Unsupported(f"symbol {expr.name!r}")
            return self._new("i_conn(xlo, xhi)")
        if expr.is_Number:
            return self._new(f"i_point({_glsl_float(float(sp.N(expr, 17)))})")
        if isinstance(expr, sp.Add):
            args = [cast(sp.Expr, arg) for arg in expr.args]
            acc = self.emit(args[0])
            for arg in args[1:]:
                acc = self._new(f"i_add({acc}, {self.emit(arg)})")
            return acc
        if isinstance(expr, sp.Mul):
            args = [cast(sp.Expr, arg) for arg in expr.args]
            acc = self.emit(args[0])
            for arg in args[1:]:
                acc = self._new(f"i_mul({acc}, {self.emit(arg)})")
            return acc
        if isinstance(expr, sp.Pow):
            base = self.emit(expr.base)
            exponent = expr.exp
            if exponent.has(sp.Symbol("x")) and not exponent.is_number:
                raise Unsupported("variable exponent")
            if exponent.is_number:
                rational = _as_rational(exponent)
                if rational is not None:
                    p_expr, q_expr = rational.as_numer_denom()
                    p, q = int(p_expr), int(q_expr)
                    if q == 1:
                        return self._new(f"i_pow_int({base}, {p})")
                    return self._new(f"i_pow_frac({base}, {p}, {q})")
                return self._new(
                    f"i_exp(i_mul(i_point({_glsl_float(float(sp.N(exponent, 17)))}), i_log({base})))"
                )
            raise Unsupported("non-numeric exponent")
        if isinstance(expr, sp.Abs):
            return self._new(f"i_abs({self.emit(_arg(expr))})")
        if isinstance(expr, sp.exp):
            return self._new(f"i_exp({self.emit(_arg(expr))})")
        if isinstance(expr, sp.log):
            if len(expr.args) == 2:  # log(arg, base)
                return self._new(f"i_logn({self.emit(_arg(expr, 1))}, {self.emit(_arg(expr))})")
            return self._new(f"i_log({self.emit(_arg(expr))})")
        if isinstance(expr, sp.sin):
            return self._new(f"i_sin({self.emit(_arg(expr))})")
        if isinstance(expr, sp.cos):
            return self._new(f"i_cos({self.emit(_arg(expr))})")
        if isinstance(expr, sp.tan):
            return self._new(f"i_tan({self.emit(_arg(expr))})")
        if isinstance(expr, sp.asin):
            return self._new(f"i_asin({self.emit(_arg(expr))})")
        if isinstance(expr, sp.acos):
            return self._new(f"i_acos({self.emit(_arg(expr))})")
        if isinstance(expr, sp.atan):
            return self._new(f"i_atan({self.emit(_arg(expr))})")
        if isinstance(expr, sp.sinh):
            return self._new(f"i_sinh({self.emit(_arg(expr))})")
        if isinstance(expr, sp.cosh):
            return self._new(f"i_cosh({self.emit(_arg(expr))})")
        if isinstance(expr, sp.tanh):
            return self._new(f"i_tanh({self.emit(_arg(expr))})")
        if isinstance(expr, sp.sec):
            return self._new(f"i_inv(i_cos({self.emit(_arg(expr))}))")
        if isinstance(expr, sp.csc):
            return self._new(f"i_inv(i_sin({self.emit(_arg(expr))}))")
        if isinstance(expr, sp.cot):
            return self._new(f"i_div(i_cos({self.emit(_arg(expr))}), i_sin({self.emit(_arg(expr))}))")
        # Anything else (floor, sign, Min/Max, Piecewise, ...) keeps the legacy renderer.
        raise Unsupported(type(expr).__name__)


_FUNCTION_START = re.compile(r"(?m)^(?:IVal|bool|float)\s+(\w+)\s*\([^;{]*\)\s*\{")


def _library_functions() -> tuple[str, list[str], dict[str, str]]:
    """Split ``INTERVAL_LIB`` into its header and top-level functions, in source order.

    The trimmer is textual on purpose: the library is GLSL, not Python, and the shader
    compiler pays for every line it has to parse.
    """
    matches = list(_FUNCTION_START.finditer(INTERVAL_LIB))
    if not matches:
        raise AssertionError("the interval library has no functions")
    header = INTERVAL_LIB[:matches[0].start()]
    order: list[str] = []
    bodies: dict[str, str] = {}
    for match in matches:
        name = match.group(1)
        open_brace = INTERVAL_LIB.find("{", match.end() - 1)
        depth = 0
        for index in range(open_brace, len(INTERVAL_LIB)):
            char = INTERVAL_LIB[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    bodies[name] = INTERVAL_LIB[match.start():index + 1]
                    order.append(name)
                    break
        else:  # pragma: no cover - the library is well formed
            raise AssertionError(f"unbalanced braces in {name}")
    return header, order, bodies


def compute_supported(expr: sp.Expr) -> bool:
    """Whether the compute interval renderer can draw this 1-D expression.

    The route is GGB's: ``ggb_eligibility.supported`` (exactly one x occurrence and a
    supported operation). The interval library must also be able to emit the expression;
    a GGB-supported operation our GLSL lacks is a capability gap, and the caller falls
    back rather than failing.
    """
    from .ggb_eligibility import supported as ggb_supported

    if not ggb_supported(expr):
        return False
    try:
        interval_glsl(expr)
    except Unsupported:
        return False
    return True


def interval_lib_for(fi_source: str) -> str:
    """The part of ``INTERVAL_LIB`` that the emitted ``FI`` actually reaches.

    Every compute shader used to embed the whole library (~15 KB) whether it needed
    ``i_sin`` or ``i_pow_int``; D3D11 then compiled all of it for every new renderer, which
    dominated export/first-frame cost. The closure keeps source order (the library is
    dependency-ordered) and includes only called functions.
    """
    header, order, bodies = _library_functions()
    needed = set(re.findall(r"\b(i_[A-Za-z0-9_]+)\s*\(", fi_source))
    closure: set[str] = set()
    stack = list(needed)
    while stack:
        name = stack.pop()
        if name in closure or name not in bodies:
            continue
        closure.add(name)
        stack.extend(re.findall(r"\b(i_[A-Za-z0-9_]+)\s*\(", bodies[name]))
    return header + "\n".join(bodies[name] for name in order if name in closure)


def interval_glsl(expr: sp.Expr) -> str:
    """Generate ``IVal FI(float xlo, float xhi)`` for ``expr`` (raises Unsupported)."""
    emitter = _Emitter()
    result = emitter.emit(expr)
    body = "\n".join(emitter.lines)
    return f"IVal FI(float xlo, float xhi) {{\n{body}\n    return {result};\n}}\n"
