"""Python reference for the current ``interval_lib.glsl`` semantics.

This mirrors the production GLSL library (not GGB's full ``IntervalSet`` yet). It exists to
compare the per-column topology with GGB's oracle fixtures and to pinpoint P1 gaps before
touching the shader. Coordinates/sentinels follow ``interval_lib.glsl``: ``IINF = 1e30``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import sympy as sp

from .interval import as_rational

__all__ = ["IVal", "IINF", "evaluate", "Unsupported"]

IINF = 1e30
TAU = 2.0 * math.pi
PI = math.pi
PI_HALF = math.pi / 2.0

EMPTY, CONNECTED, INVERTED, WHOLE = 0, 1, 2, 3


class Unsupported(Exception):
    pass


@dataclass(frozen=True)
class IVal:
    lo: float
    hi: float
    kind: int


def empty() -> IVal:
    return IVal(1.0, -1.0, EMPTY)


def conn(lo: float, hi: float) -> IVal:
    if math.isnan(lo) or math.isnan(hi) or lo > hi:
        return empty()
    if lo <= -IINF and hi >= IINF:
        return IVal(-IINF, IINF, WHOLE)
    return IVal(max(-IINF, min(IINF, lo)), max(-IINF, min(IINF, hi)), CONNECTED)


def invset(lo: float, hi: float) -> IVal:
    if math.isnan(lo) or math.isnan(hi) or lo > hi:
        return empty()
    return IVal(max(-IINF, min(IINF, lo)), max(-IINF, min(IINF, hi)), INVERTED)


def whole() -> IVal:
    return IVal(-IINF, IINF, WHOLE)


def point(v: float) -> IVal:
    return conn(v, v)


def finite(v: float) -> bool:
    return not math.isnan(v) and abs(v) < IINF


def union(a: IVal, b: IVal) -> IVal:
    if a.kind == EMPTY:
        return b
    if b.kind == EMPTY:
        return a
    if a.kind == WHOLE or b.kind == WHOLE:
        return whole()
    if a.kind == CONNECTED and b.kind == CONNECTED:
        if a.lo <= b.hi and b.lo <= a.hi:
            return conn(min(a.lo, b.lo), max(a.hi, b.hi))
        return invset(a.hi, b.lo) if a.hi < b.lo else invset(b.hi, a.lo)
    if a.kind == INVERTED and b.kind == INVERTED:
        lo, hi = max(a.lo, b.lo), min(a.hi, b.hi)
        return invset(lo, hi) if lo <= hi else whole()
    if a.kind == CONNECTED:
        a, b = b, a
    if b.hi < a.lo or b.lo > a.hi:
        return a
    if b.lo <= a.lo and b.hi >= a.hi:
        return whole()
    if b.lo <= a.lo:
        return invset(b.hi, a.hi) if b.hi < a.hi else whole()
    if b.hi >= a.hi:
        return invset(a.lo, b.lo) if b.lo > a.lo else whole()
    return whole()


def neg(a: IVal) -> IVal:
    if a.kind in (EMPTY, WHOLE):
        return a
    return IVal(-a.hi, -a.lo, a.kind)


def add_inv_conn(inv: IVal, con: IVal) -> IVal:
    left = inv.lo + con.hi
    right = inv.hi + con.lo
    return invset(left, right) if left < right else whole()


def add(a: IVal, b: IVal) -> IVal:
    if a.kind == EMPTY or b.kind == EMPTY:
        return empty()
    if a.kind == WHOLE or b.kind == WHOLE:
        return whole()
    if a.kind == INVERTED and b.kind == INVERTED:
        left, right = a.lo + b.lo, a.hi + b.hi
        return invset(left, right) if left < right else whole()
    if a.kind == INVERTED:
        return add_inv_conn(a, b)
    if b.kind == INVERTED:
        return add_inv_conn(b, a)
    return conn(a.lo + b.lo, a.hi + b.hi)


def sub(a: IVal, b: IVal) -> IVal:
    return add(a, neg(b))


def mul_conn(a: IVal, b: IVal) -> IVal:
    values = (a.lo * b.lo, a.lo * b.hi, a.hi * b.lo, a.hi * b.hi)
    return conn(min(values), max(values))


def mul(a: IVal, b: IVal) -> IVal:
    if a.kind == EMPTY or b.kind == EMPTY:
        return empty()
    az = a.kind == CONNECTED and a.lo == 0.0 and a.hi == 0.0
    bz = b.kind == CONNECTED and b.lo == 0.0 and b.hi == 0.0
    if az or bz:
        return point(0.0)
    if a.kind == WHOLE or b.kind == WHOLE:
        return whole()
    if a.kind == INVERTED:
        left = mul_conn(conn(-IINF, a.lo), b)
        right = mul_conn(conn(a.hi, IINF), b)
        return union(left, right)
    if b.kind == INVERTED:
        left = mul_conn(conn(-IINF, b.lo), a)
        right = mul_conn(conn(b.hi, IINF), a)
        return union(left, right)
    return mul_conn(a, b)


def inv_conn(lo: float, hi: float) -> IVal:
    if lo > hi:
        return empty()
    has_zero = lo <= 0.0 <= hi
    if has_zero:
        if lo < 0.0 and hi > 0.0:
            return invset(1.0 / lo, 1.0 / hi)
        if lo == 0.0 and hi > 0.0:
            return conn(1.0 / hi, IINF)
        if lo < 0.0 and hi == 0.0:
            return conn(-IINF, 1.0 / lo)
        return empty()
    return conn(1.0 / hi, 1.0 / lo)


def inv(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind == WHOLE:
        return invset(0.0, 0.0)
    if a.kind == INVERTED:
        return union(inv_conn(-IINF, a.lo), inv_conn(a.hi, IINF))
    return inv_conn(a.lo, a.hi)


def div(a: IVal, b: IVal) -> IVal:
    return mul(a, inv(b))


def sin(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind != CONNECTED:
        return conn(-1.0, 1.0)
    width = a.hi - a.lo
    if not finite(a.lo) or not finite(a.hi) or width >= TAU:
        return conn(-1.0, 1.0)
    s0, s1 = math.sin(a.lo), math.sin(a.hi)
    lo, hi = min(s0, s1), max(s0, s1)
    k = math.ceil((a.lo - PI_HALF) / TAU)
    if PI_HALF + k * TAU <= a.hi:
        hi = 1.0
    k = math.ceil((a.lo - 3.0 * PI_HALF) / TAU)
    if 3.0 * PI_HALF + k * TAU <= a.hi:
        lo = -1.0
    return conn(lo, hi)


def cos(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind != CONNECTED:
        return conn(-1.0, 1.0)
    width = a.hi - a.lo
    if not finite(a.lo) or not finite(a.hi) or width >= TAU:
        return conn(-1.0, 1.0)
    c0, c1 = math.cos(a.lo), math.cos(a.hi)
    lo, hi = min(c0, c1), max(c0, c1)
    k = math.ceil(a.lo / TAU)
    if k * TAU <= a.hi:
        hi = 1.0
    k = math.ceil((a.lo - PI) / TAU)
    if PI + k * TAU <= a.hi:
        lo = -1.0
    return conn(lo, hi)


def tan(a: IVal) -> IVal:
    return div(sin(a), cos(a))


def asin_conn(a: IVal) -> IVal:
    if a.lo > 1.0 or a.hi < -1.0:
        return empty()
    return conn(math.asin(max(a.lo, -1.0)), math.asin(min(a.hi, 1.0)))


def acos_conn(a: IVal) -> IVal:
    if a.lo > 1.0 or a.hi < -1.0:
        return empty()
    return conn(math.acos(min(a.hi, 1.0)), math.acos(max(a.lo, -1.0)))


def asin(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind == WHOLE:
        return conn(-PI_HALF, PI_HALF)
    if a.kind == INVERTED:
        return union(asin_conn(conn(-IINF, a.lo)), asin_conn(conn(a.hi, IINF)))
    return asin_conn(a)


def acos(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind == WHOLE:
        return conn(0.0, PI)
    if a.kind == INVERTED:
        return union(acos_conn(conn(-IINF, a.lo)), acos_conn(conn(a.hi, IINF)))
    return acos_conn(a)


def atan(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind == WHOLE:
        return conn(-PI_HALF, PI_HALF)
    if a.kind == INVERTED:
        return invset(math.atan(a.lo), math.atan(a.hi))
    return conn(math.atan(a.lo), math.atan(a.hi))


def _exp(v: float) -> float:
    if math.isnan(v):
        return math.nan
    if v > 709.0:
        return IINF
    if v < -745.0:
        return 0.0
    return math.exp(v)


def _sinh(v: float) -> float:
    if math.isnan(v):
        return math.nan
    if v > 710.0:
        return IINF
    if v < -710.0:
        return -IINF
    return math.sinh(v)


def _cosh(v: float) -> float:
    if math.isnan(v):
        return math.nan
    if abs(v) > 710.0:
        return IINF
    return math.cosh(v)


def exp(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind != CONNECTED:
        return conn(0.0, IINF)
    return conn(_exp(a.lo), _exp(a.hi))


def log_conn(a: IVal) -> IVal:
    if a.hi <= 0.0:
        return empty()
    if a.lo <= 0.0:
        return conn(-IINF, math.log(a.hi))
    return conn(math.log(a.lo), math.log(a.hi))


def log(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind == WHOLE:
        return whole()
    if a.kind == INVERTED:
        return union(log_conn(conn(-IINF, a.lo)), log_conn(conn(a.hi, IINF)))
    return log_conn(a)


def absval(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind == WHOLE:
        return conn(0.0, IINF)
    if a.kind == INVERTED:
        if a.lo >= 0.0 or a.hi <= 0.0:
            return conn(0.0, IINF)
        return conn(min(-a.lo, a.hi), IINF)
    if a.lo >= 0.0:
        return a
    if a.hi <= 0.0:
        return conn(-a.hi, -a.lo)
    return conn(0.0, max(-a.lo, a.hi))


def rroot(v: float, n: int) -> float:
    root = abs(v) ** (1.0 / n)
    return -root if v < 0.0 else root


def nroot_conn(a: IVal, n: int) -> IVal:
    if n % 2 == 0:
        if a.hi < 0.0:
            return empty()
        return conn(max(a.lo, 0.0) ** (1.0 / n), a.hi ** (1.0 / n))
    return conn(rroot(a.lo, n), rroot(a.hi, n))


def nroot(a: IVal, n: int) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind == WHOLE:
        return conn(0.0, IINF) if n % 2 == 0 else whole()
    if a.kind == INVERTED:
        return union(nroot_conn(conn(-IINF, a.lo), n), nroot_conn(conn(a.hi, IINF), n))
    return nroot_conn(a, n)


def pow_int_conn(a: IVal, n: int) -> IVal:
    lo, hi = a.lo, a.hi
    odd = n % 2 == 1
    if hi < 0.0:
        low, high = abs(hi) ** n, abs(lo) ** n
        return conn(-high, -low) if odd else conn(low, high)
    if lo < 0.0:
        if odd:
            return conn(-(abs(lo) ** n), hi ** n)
        return conn(0.0, max(abs(lo), hi) ** n)
    return conn(lo**n, hi**n)


def pow_positive(a: IVal, n: int) -> IVal:
    if n == 1:
        return a
    if a.kind == WHOLE:
        return conn(0.0, IINF) if n % 2 == 0 else whole()
    if a.kind == INVERTED:
        return union(pow_int_conn(conn(-IINF, a.lo), n),
                     pow_int_conn(conn(a.hi, IINF), n))
    return pow_int_conn(a, n)


def pow_int(a: IVal, n: int) -> IVal:
    if a.kind == EMPTY:
        return a
    if n == 0:
        return point(1.0)
    if n < 0:
        return inv(pow_positive(a, -n))
    return pow_positive(a, n)


def pow_frac(a: IVal, p: int, q: int) -> IVal:
    if q == 1:
        return pow_int(a, p)
    result = nroot(a, q)
    return inv(pow_int(result, -p)) if p < 0 else pow_int(result, p)


def sinh(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind == WHOLE:
        return whole()
    if a.kind == INVERTED:
        return union(conn(_sinh(-IINF), _sinh(a.lo)),
                     conn(_sinh(a.hi), _sinh(IINF)))
    return conn(_sinh(a.lo), _sinh(a.hi))


def cosh(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind == WHOLE:
        return conn(1.0, IINF)
    if a.kind == INVERTED:
        if a.lo >= 0.0 or a.hi <= 0.0:
            return conn(1.0, IINF)
        return conn(_cosh(min(-a.lo, a.hi)), IINF)
    if a.hi < 0.0:
        return conn(_cosh(a.hi), _cosh(a.lo))
    if a.lo >= 0.0:
        return conn(_cosh(a.lo), _cosh(a.hi))
    return conn(1.0, _cosh(max(-a.lo, a.hi)))


def tanh(a: IVal) -> IVal:
    if a.kind == EMPTY:
        return a
    if a.kind == WHOLE:
        return conn(-1.0, 1.0)
    if a.kind == INVERTED:
        return invset(math.tanh(a.lo), math.tanh(a.hi))
    return conn(math.tanh(a.lo), math.tanh(a.hi))


def evaluate(expr: sp.Expr, x_lo: float, x_hi: float) -> IVal:
    return _eval(expr, conn(x_lo, x_hi))


def _eval(expr: sp.Expr, xv: IVal) -> IVal:
    if isinstance(expr, sp.Symbol):
        if expr.name != "x":
            raise Unsupported(expr.name)
        return xv
    if expr.is_Number:
        return point(float(sp.N(expr, 17)))
    if isinstance(expr, sp.Add):
        result = _eval(expr.args[0], xv)
        for arg in expr.args[1:]:
            result = add(result, _eval(arg, xv))
        return result
    if isinstance(expr, sp.Mul):
        result = _eval(expr.args[0], xv)
        for arg in expr.args[1:]:
            result = mul(result, _eval(arg, xv))
        return result
    if isinstance(expr, sp.Pow):
        base = _eval(expr.base, xv)
        exponent = expr.exp
        if exponent.has(sp.Symbol("x")) and not exponent.is_number:
            raise Unsupported("variable exponent")
        if exponent.is_number:
            rational = as_rational(exponent)
            if rational is not None:
                p, q = rational.as_numer_denom()
                return pow_frac(base, int(p), int(q))
            return exp(mul(point(float(sp.N(exponent, 17))), log(base)))
        raise Unsupported("non-numeric exponent")
    if isinstance(expr, sp.Abs):
        return absval(_eval(expr.args[0], xv))
    if isinstance(expr, sp.exp):
        return exp(_eval(expr.args[0], xv))
    if isinstance(expr, sp.log):
        if len(expr.args) == 2:
            return div(log(_eval(expr.args[0], xv)), log(_eval(expr.args[1], xv)))
        return log(_eval(expr.args[0], xv))
    if isinstance(expr, sp.sin):
        return sin(_eval(expr.args[0], xv))
    if isinstance(expr, sp.cos):
        return cos(_eval(expr.args[0], xv))
    if isinstance(expr, sp.tan):
        return tan(_eval(expr.args[0], xv))
    if isinstance(expr, sp.asin):
        return asin(_eval(expr.args[0], xv))
    if isinstance(expr, sp.acos):
        return acos(_eval(expr.args[0], xv))
    if isinstance(expr, sp.atan):
        return atan(_eval(expr.args[0], xv))
    if isinstance(expr, sp.sinh):
        return sinh(_eval(expr.args[0], xv))
    if isinstance(expr, sp.cosh):
        return cosh(_eval(expr.args[0], xv))
    if isinstance(expr, sp.tanh):
        return tanh(_eval(expr.args[0], xv))
    if isinstance(expr, sp.sec):
        return inv(cos(_eval(expr.args[0], xv)))
    if isinstance(expr, sp.csc):
        return inv(sin(_eval(expr.args[0], xv)))
    if isinstance(expr, sp.cot):
        return div(cos(_eval(expr.args[0], xv)), sin(_eval(expr.args[0], xv)))
    raise Unsupported(type(expr).__name__)
