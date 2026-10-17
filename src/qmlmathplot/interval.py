"""Interval arithmetic for the 1-D renderer (the GeoGebra column idea, on the GPU).

GeoGebra samples one *interval* of x per pixel column and evaluates the whole expression in
interval arithmetic. The result is a conservative y-enclosure with a topology: empty,
connected, inverted (two rays around a gap), or whole. This module generates the same
evaluation as GLSL from a sympy expression, so the renderer keeps its per-pixel GPU march for
the stroke while the envelope band becomes exact instead of a heuristic.

The GLSL side is deliberately small: a value is ``(lo, hi, kind)``; an inverted set stores the
*excluded* gap; sentinel ``IINF`` stands for infinity (real infinities are not needed and make
NaN handling worse). Anything this module cannot evaluate raises :class:`Unsupported`, and the
caller falls back to the previous shader (sampled envelope band + boundary ray).

The interval library is a port of the relevant parts of GeoGebra's
``org.geogebra.common.kernel.interval`` (``IntervalSet`` topology, ``IntervalSinCos``,
``IntervalDivide``, ``IntervalRoot``, ``IntervalMiscOperands``) reduced to what a fragment
shader needs. It is conservative: dependency overestimation may widen a band, never shrink it.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import cast

import sympy as sp

__all__ = [
    "INTERVAL_LIB",
    "Unsupported",
    "as_rational",
    "has_dependent_quotient",
    "has_sign_pole",
    "interval_glsl",
]


class Unsupported(Exception):
    """The expression has an operation the interval evaluator does not implement."""


# --------------------------------------------------------------------------------------------
# GLSL library
# --------------------------------------------------------------------------------------------
INTERVAL_LIB = r"""
struct IVal { float lo; float hi; int kind; };
#define IK_EMPTY 0
#define IK_CONNECTED 1
#define IK_INVERTED 2
#define IK_WHOLE 3
#define IINF 1e30
#define IPI 3.141592653589793
#define IPI_HALF 1.5707963267948966

IVal i_empty() { return IVal(1.0, -1.0, IK_EMPTY); }
IVal i_conn(float lo, float hi) {
    if (!(lo == lo) || !(hi == hi) || lo > hi) return i_empty();
    if (lo <= -IINF && hi >= IINF) return IVal(-IINF, IINF, IK_WHOLE);
    return IVal(clamp(lo, -IINF, IINF), clamp(hi, -IINF, IINF), IK_CONNECTED);
}
IVal i_invset(float lo, float hi) {
    if (!(lo == lo) || !(hi == hi) || lo > hi) return i_empty();
    return IVal(clamp(lo, -IINF, IINF), clamp(hi, -IINF, IINF), IK_INVERTED);
}
IVal i_whole() { return IVal(-IINF, IINF, IK_WHOLE); }
IVal i_point(float v) { return i_conn(v, v); }
bool i_fin(float v) { return v == v && abs(v) < IINF; }

IVal i_union(IVal a, IVal b) {
    if (a.kind == IK_EMPTY) return b;
    if (b.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE || b.kind == IK_WHOLE) return i_whole();
    if (a.kind == IK_CONNECTED && b.kind == IK_CONNECTED) {
        if (a.lo <= b.hi && b.lo <= a.hi)
            return i_conn(min(a.lo, b.lo), max(a.hi, b.hi));
        return (a.hi < b.lo) ? i_invset(a.hi, b.lo) : i_invset(b.hi, a.lo);
    }
    if (a.kind == IK_INVERTED && b.kind == IK_INVERTED) {
        float lo = max(a.lo, b.lo);
        float hi = min(a.hi, b.hi);
        return (lo <= hi) ? i_invset(lo, hi) : i_whole();
    }
    // connected U inverted: fill part of the gap if that is representable
    if (a.kind == IK_CONNECTED) { IVal t = a; a = b; b = t; }
    if (b.hi < a.lo || b.lo > a.hi) return a;       // b lies in one ray
    if (b.lo <= a.lo && b.hi >= a.hi) return i_whole();
    if (b.lo <= a.lo) return (b.hi < a.hi) ? i_invset(b.hi, a.hi) : i_whole();
    if (b.hi >= a.hi) return (b.lo > a.lo) ? i_invset(a.lo, b.lo) : i_whole();
    return i_whole();                                // b sits inside the gap
}

IVal i_neg(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE) return a;
    return IVal(-a.hi, -a.lo, a.kind);
}

IVal i_add_inv_conn(IVal inv, IVal con) {
    float l = inv.lo + con.hi;          // (-inf, gap.lo] + [con.lo, con.hi]
    float r = inv.hi + con.lo;          // [gap.hi, inf) + [con.lo, con.hi]
    return (l < r) ? i_invset(l, r) : i_whole();
}

IVal i_add(IVal a, IVal b) {
    if (a.kind == IK_EMPTY || b.kind == IK_EMPTY) return i_empty();
    if (a.kind == IK_WHOLE || b.kind == IK_WHOLE) return i_whole();
    if (a.kind == IK_INVERTED && b.kind == IK_INVERTED) {
        float l = a.lo + b.lo;
        float r = a.hi + b.hi;
        return (l < r) ? i_invset(l, r) : i_whole();
    }
    if (a.kind == IK_INVERTED) return i_add_inv_conn(a, b);
    if (b.kind == IK_INVERTED) return i_add_inv_conn(b, a);
    return i_conn(a.lo + b.lo, a.hi + b.hi);
}

IVal i_sub(IVal a, IVal b) { return i_add(a, i_neg(b)); }

IVal i_mul_conn(IVal a, IVal b) {
    float p1 = a.lo * b.lo;
    float p2 = a.lo * b.hi;
    float p3 = a.hi * b.lo;
    float p4 = a.hi * b.hi;
    return i_conn(min(min(p1, p2), min(p3, p4)), max(max(p1, p2), max(p3, p4)));
}

IVal i_mul(IVal a, IVal b) {
    if (a.kind == IK_EMPTY || b.kind == IK_EMPTY) return i_empty();
    bool az = (a.kind == IK_CONNECTED && a.lo == 0.0 && a.hi == 0.0);
    bool bz = (b.kind == IK_CONNECTED && b.lo == 0.0 && b.hi == 0.0);
    if (az || bz) return i_point(0.0);
    if (a.kind == IK_WHOLE || b.kind == IK_WHOLE) return i_whole();
    if (a.kind == IK_INVERTED) {
        IVal l = i_mul_conn(i_conn(-IINF, a.lo), b);
        IVal r = i_mul_conn(i_conn(a.hi, IINF), b);
        return i_union(l, r);
    }
    if (b.kind == IK_INVERTED) {
        IVal l = i_mul_conn(i_conn(-IINF, b.lo), a);
        IVal r = i_mul_conn(i_conn(b.hi, IINF), a);
        return i_union(l, r);
    }
    return i_mul_conn(a, b);
}

IVal i_inv_conn(float lo, float hi) {
    if (lo > hi) return i_empty();
    bool has0 = (lo <= 0.0 && hi >= 0.0);
    if (has0) {
        if (lo < 0.0 && hi > 0.0) return i_invset(1.0 / lo, 1.0 / hi);
        if (lo == 0.0 && hi > 0.0) return i_conn(1.0 / hi, IINF);
        if (lo < 0.0 && hi == 0.0) return i_conn(-IINF, 1.0 / lo);
        return i_empty();
    }
    return i_conn(1.0 / hi, 1.0 / lo);
}

IVal i_inv(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE) return i_invset(0.0, 0.0);
    if (a.kind == IK_INVERTED) {
        return i_union(i_inv_conn(-IINF, a.lo), i_inv_conn(a.hi, IINF));
    }
    return i_inv_conn(a.lo, a.hi);
}

IVal i_div(IVal a, IVal b) { return i_mul(a, i_inv(b)); }

IVal i_sin(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind != IK_CONNECTED) return i_conn(-1.0, 1.0);
    float w = a.hi - a.lo;
    if (!i_fin(a.lo) || !i_fin(a.hi) || w >= TAU) return i_conn(-1.0, 1.0);
    float s0 = SIN(a.lo), s1 = SIN(a.hi);
    float lo = min(s0, s1), hi = max(s0, s1);
    float k = ceil((a.lo - IPI_HALF) / TAU);
    if (IPI_HALF + k * TAU <= a.hi) hi = 1.0;
    k = ceil((a.lo - 3.0 * IPI_HALF) / TAU);
    if (3.0 * IPI_HALF + k * TAU <= a.hi) lo = -1.0;
    return i_conn(lo, hi);
}

IVal i_cos(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind != IK_CONNECTED) return i_conn(-1.0, 1.0);
    float w = a.hi - a.lo;
    if (!i_fin(a.lo) || !i_fin(a.hi) || w >= TAU) return i_conn(-1.0, 1.0);
    float c0 = COS(a.lo), c1 = COS(a.hi);
    float lo = min(c0, c1), hi = max(c0, c1);
    float k = ceil((a.lo - 0.0) / TAU);
    if (k * TAU <= a.hi) hi = 1.0;
    k = ceil((a.lo - IPI) / TAU);
    if (IPI + k * TAU <= a.hi) lo = -1.0;
    return i_conn(lo, hi);
}

IVal i_tan(IVal a) { return i_div(i_sin(a), i_cos(a)); }

IVal i_asin_conn(IVal a) {
    if (a.lo > 1.0 || a.hi < -1.0) return i_empty();
    float lo = max(a.lo, -1.0);
    float hi = min(a.hi, 1.0);
    return i_conn(asin(lo), asin(hi));
}

IVal i_acos_conn(IVal a) {
    if (a.lo > 1.0 || a.hi < -1.0) return i_empty();
    float lo = max(a.lo, -1.0);
    float hi = min(a.hi, 1.0);
    return i_conn(acos(hi), acos(lo));
}

IVal i_asin(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE) return i_conn(-IPI_HALF, IPI_HALF);
    if (a.kind == IK_INVERTED) {
        return i_union(i_asin_conn(i_conn(-IINF, a.lo)),
                       i_asin_conn(i_conn(a.hi, IINF)));
    }
    return i_asin_conn(a);
}

IVal i_acos(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE) return i_conn(0.0, IPI);
    if (a.kind == IK_INVERTED) {
        return i_union(i_acos_conn(i_conn(-IINF, a.lo)),
                       i_acos_conn(i_conn(a.hi, IINF)));
    }
    return i_acos_conn(a);
}

IVal i_atan(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE) return i_conn(-IPI_HALF, IPI_HALF);
    if (a.kind == IK_INVERTED) {
        return i_invset(atan(a.lo), atan(a.hi));
    }
    return i_conn(atan(a.lo), atan(a.hi));
}

IVal i_exp(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind != IK_CONNECTED) return i_conn(0.0, IINF);
    return i_conn(exp(a.lo), exp(a.hi));
}

IVal i_log_conn(IVal a) {
    if (a.hi <= 0.0) return i_empty();
    if (a.lo <= 0.0) return i_conn(-IINF, log(a.hi));
    return i_conn(log(a.lo), log(a.hi));
}

IVal i_log(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE) return i_whole();
    if (a.kind == IK_INVERTED) {
        return i_union(i_log_conn(i_conn(-IINF, a.lo)),
                       i_log_conn(i_conn(a.hi, IINF)));
    }
    return i_log_conn(a);
}

IVal i_logn(IVal base, IVal arg) { return i_div(i_log(arg), i_log(base)); }

IVal i_abs(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE) return i_conn(0.0, IINF);
    if (a.kind == IK_INVERTED) {
        float lo = a.lo, hi = a.hi;
        if (lo >= 0.0 || hi <= 0.0) return i_conn(0.0, IINF);
        return i_conn(min(-lo, hi), IINF);
    }
    if (a.lo >= 0.0) return a;
    if (a.hi <= 0.0) return i_conn(-a.hi, -a.lo);
    return i_conn(0.0, max(-a.lo, a.hi));
}

float i_rroot(float v, float n) {
    float r = pow(abs(v), 1.0 / n);
    return v < 0.0 ? -r : r;
}

IVal i_nroot_conn(IVal a, int n) {
    if ((n % 2) == 0) {
        if (a.hi < 0.0) return i_empty();
        float lo = max(a.lo, 0.0);
        return i_conn(pow(lo, 1.0 / float(n)), pow(a.hi, 1.0 / float(n)));
    }
    return i_conn(i_rroot(a.lo, float(n)), i_rroot(a.hi, float(n)));
}

IVal i_nroot(IVal a, int n) {
    if (a.kind == IK_EMPTY) return a;
    bool even = (n % 2) == 0;
    if (a.kind == IK_WHOLE) return even ? i_conn(0.0, IINF) : i_whole();
    if (a.kind == IK_INVERTED) {
        return i_union(i_nroot_conn(i_conn(-IINF, a.lo), n),
                       i_nroot_conn(i_conn(a.hi, IINF), n));
    }
    return i_nroot_conn(a, n);
}

IVal i_pow_int_conn(IVal a, int n) {
    float lo = a.lo, hi = a.hi;
    bool odd = (n % 2) == 1;
    if (hi < 0.0) {
        float l = pow(-hi, float(n));
        float h = pow(-lo, float(n));
        return odd ? i_conn(-h, -l) : i_conn(l, h);
    }
    if (lo < 0.0) {
        if (odd) return i_conn(-pow(-lo, float(n)), pow(hi, float(n)));
        return i_conn(0.0, pow(max(-lo, hi), float(n)));
    }
    return i_conn(pow(lo, float(n)), pow(hi, float(n)));
}

IVal i_pow_positive(IVal a, int n) {
    if (n == 1) return a;
    if (a.kind == IK_WHOLE) return ((n % 2) == 0) ? i_conn(0.0, IINF) : i_whole();
    if (a.kind == IK_INVERTED) {
        return i_union(i_pow_int_conn(i_conn(-IINF, a.lo), n),
                       i_pow_int_conn(i_conn(a.hi, IINF), n));
    }
    return i_pow_int_conn(a, n);
}

IVal i_pow_int(IVal a, int n) {
    if (a.kind == IK_EMPTY) return a;
    if (n == 0) return i_point(1.0);
    if (n < 0) return i_inv(i_pow_positive(a, -n));
    return i_pow_positive(a, n);
}

IVal i_pow_frac(IVal a, int p, int q) {
    if (q == 1) return i_pow_int(a, p);
    IVal r = i_nroot(a, q);
    if (p < 0) return i_inv(i_pow_int(r, -p));
    return i_pow_int(r, p);
}

IVal i_sinh_conn(IVal a) { return i_conn(sinh(a.lo), sinh(a.hi)); }

IVal i_sinh(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE) return i_whole();
    if (a.kind == IK_INVERTED) {
        return i_union(i_sinh_conn(i_conn(-IINF, a.lo)),
                       i_sinh_conn(i_conn(a.hi, IINF)));
    }
    return i_sinh_conn(a);
}

IVal i_cosh(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE) return i_conn(1.0, IINF);
    if (a.kind == IK_INVERTED) {
        if (a.lo >= 0.0 || a.hi <= 0.0) return i_conn(1.0, IINF);
        return i_conn(cosh(min(-a.lo, a.hi)), IINF);
    }
    if (a.hi < 0.0) return i_conn(cosh(a.hi), cosh(a.lo));
    if (a.lo >= 0.0) return i_conn(cosh(a.lo), cosh(a.hi));
    return i_conn(1.0, cosh(max(-a.lo, a.hi)));
}

IVal i_tanh(IVal a) {
    if (a.kind == IK_EMPTY) return a;
    if (a.kind == IK_WHOLE) return i_conn(-1.0, 1.0);
    if (a.kind == IK_INVERTED) return i_invset(tanh(a.lo), tanh(a.hi));
    return i_conn(tanh(a.lo), tanh(a.hi));
}
"""


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


def interval_glsl(expr: sp.Expr) -> str:
    """Generate ``IVal FI(float xlo, float xhi)`` for ``expr`` (raises Unsupported)."""
    emitter = _Emitter()
    result = emitter.emit(expr)
    body = "\n".join(emitter.lines)
    return f"IVal FI(float xlo, float xhi) {{\n{body}\n    return {result};\n}}\n"


# --------------------------------------------------------------------------------------------
# Pole topology (which WHOLE results are safe to fill)
# --------------------------------------------------------------------------------------------
def _pole_factors(expr: sp.Expr):
    out: list[sp.Expr] = []
    if isinstance(expr, sp.Pow) and expr.exp.is_number and expr.exp.is_negative:
        out.append(expr.base)
    if isinstance(expr, sp.tan | sp.sec):
        out.append(sp.cos(expr.args[0]))
    elif isinstance(expr, sp.cot | sp.csc):
        out.append(sp.sin(expr.args[0]))
    for arg in expr.args:
        out.extend(_pole_factors(cast(sp.Expr, arg)))
    return out


def _changes_sign(factor: sp.Expr) -> bool:
    # Even powers and absolute values do not change sign at a zero; their poles send both
    # branches to the same infinity and a filled column is the true one-sided image.
    if isinstance(factor, sp.Pow) and factor.exp.is_Integer and int(factor.exp) % 2 == 0:
        return False
    return not factor.has(sp.Abs)


def has_sign_pole(expr: sp.Expr) -> bool:
    """Whether a WHOLE column may be filled: a sign-changing pole sends branches to ±inf.

    ``tan`` (cos factor) and ``1/x`` qualify; ``x/sqrt(x**2)`` does not (its factor is x**2), so
    a jump is left to the march's join invariant instead of being flooded.
    """
    return any(_changes_sign(factor) for factor in _pole_factors(expr))


def has_dependent_quotient(expr: sp.Expr) -> bool:
    """Whether x survives outside a denominator that also contains x.

    That is the pattern whose interval extension explodes even though the function is bounded:
    ``x/sqrt(x**2)`` is ±1, but ``x * 1/[0,h]`` is unbounded. ``sin(1/x)`` has x only inside
    the denominator and is not flagged. Used to veto an unbounded *band* from such a column;
    the march still draws the true jump.
    """
    has_denominator = False

    def strip(node: sp.Expr) -> sp.Expr:
        nonlocal has_denominator
        if isinstance(node, sp.Pow) and node.exp.is_number and node.exp.is_negative:
            has_denominator = True
            return sp.Integer(1)
        if not node.args:
            return node
        return node.func(*[strip(cast(sp.Expr, arg)) for arg in node.args])

    outside = strip(expr)
    return has_denominator and sp.Symbol("x") in outside.free_symbols
