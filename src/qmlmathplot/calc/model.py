"""Model layer: expression -> GLSL and the shader source templates.

This layer imports no Qt and can be tested standalone without a GUI; both the camera
(camera.py) and the View (the QRhi pass) build on it.

The drawing model is **implicit (signed-distance)**: the fragment shader computes, per
pixel, the first-order screen-space distance "to the curve", sampling it by **marching
along the curve at uniform screen-space arc length** (so steep parts keep their width
and a pole/discontinuity connector can never be formed — see the comment above
``shaders/implicit.frag`` or the compute path). Cost per frame is proportional to the pixel count and independent
of function frequency (oscillatory functions cannot drag the frame rate down through
aliasing): a column the march cannot resolve is filled as a [min,max] envelope band.
"""

from __future__ import annotations

import re

import sympy as sp
from sympy.printing.glsl import GLSLPrinter
from sympy.printing.precedence import PRECEDENCE

from ..shaderbuild import render, shader_source
from .interval import (
    as_rational,
)

__all__ = [
    "VERTEX_SHADER",
    "dfunc_glsl",
    "func_glsl",
    "implicit_shader_sources",
]


class _PlotGLSLPrinter(GLSLPrinter):
    """Patch for the GLSL printer:

    1. Small integer powers do not emit pow(). GLSL's pow(x, y) is undefined for x < 0
       (many drivers simply return NaN), yet sympy prints x**2 as pow(x, 2.0). Emit
       repeated multiplication here instead; it is also faster.
    2. Optionally print a symbol as ``(name)``: expressions are inlined into
       ``#define F(u) (...)``, and the macro is **text substitution**, so ``1.0/u``
       with ``F(x - h)`` would become ``1.0/x - h`` — one missing pair of parentheses
       changes the semantics.
    """

    def __init__(self, wrap_symbol: str | None = None) -> None:
        super().__init__()
        self._wrap_symbol = wrap_symbol

    def _print_Symbol(self, expr: sp.Symbol) -> str:
        text = super()._print_Symbol(expr)
        return f"({text})" if expr.name == self._wrap_symbol else text

    def _print_Pow(self, expr: sp.Expr) -> str:
        exp = expr.exp
        if exp.is_Integer and 2 <= abs(int(exp)) <= 8:
            n = int(exp)
            base = self.parenthesize(expr.base, PRECEDENCE["Mul"])
            product = "*".join([base] * abs(n))
            # Always parenthesize: the product may end up inside a denominator (sympy
            # splits x**-2 into 1/x**2), and without parentheses it prints "a/x*x", which
            # parses as (a/x)*x.
            return f"(1.0/({product}))" if n < 0 else f"({product})"
        rational = as_rational(exp) if exp.is_number else None
        if rational is not None:
            p_expr, q_expr = rational.as_numer_denom()
            p, q = int(p_expr), int(q_expr)
            if q != 1:
                base = self.parenthesize(expr.base, PRECEDENCE["Pow"])
                value = f"{float(rational):.9g}"
                if q % 2 == 1:
                    # Real q-th root: GLSL pow() is undefined for a negative base, and a
                    # cube root (etc.) is defined there. An even numerator is nonnegative.
                    if p % 2 == 0:
                        return f"pow(abs({base}), {value})"
                    return f"_real_pow({base}, {value})"
                # Even denominator: the real function is x >= 0 only; pow() is NaN below.
                return f"pow({base}, {value})"
        return super()._print_Pow(expr)


# The GPU's built-in sin/cos are unreliable for large arguments (measured on Intel D3D11),
# while functions like sin(1/x) can reach arguments in the hundreds near a singularity.
# Fold the argument into [0, 2π) before calling the built-in; all implementations are
# accurate for small arguments.
_SIN_COS = re.compile(r"(?<![A-Za-z0-9_])(sin|cos)\(")


def _wrap_trig(text: str) -> str:
    return _SIN_COS.sub(lambda m: ("SIN(" if m.group(1) == "sin" else "COS("), text)


def func_glsl(expr: sp.Expr, name: str = "x", wrap: bool = False) -> str:
    """Print a sympy expression as a GLSL expression (returns the right-hand side of f(x)).

    name renames the variable: in the shader ``#define F(u) (...)`` needs it printed as u.
    wrap=True prints that variable as ``(u)``, making it safe for macro text substitution
    (see _PlotGLSLPrinter). sin/cos are wrapped as the template's SIN/COS (with argument
    reduction; see the template comment).
    """
    if name != "x":
        expr = expr.subs(sp.Symbol("x"), sp.Symbol(name))
    printer = _PlotGLSLPrinter(wrap_symbol=name if wrap else None)
    return _wrap_trig(printer.doprint(expr))


def _pole_factors(expr: sp.Expr) -> list[sp.Expr]:
    """Collect "pole denominators": where such a factor changes sign, the function jumps
    from +∞ to -∞.

    Only **sign-changing** factors (x, cos(x), …) are collected: a pole like 1/x² does not
    change sign and needs no gap — both sides tend to +∞, so drawing connects them
    naturally.
    """
    out: list[sp.Expr] = []
    if isinstance(expr, sp.Pow) and expr.exp.is_number and expr.exp.is_negative:
        out.append(expr.base)
    if isinstance(expr, sp.tan | sp.sec):
        out.append(sp.cos(expr.args[0]))
    elif isinstance(expr, sp.cot | sp.csc):
        out.append(sp.sin(expr.args[0]))
    for arg in expr.args:
        out.extend(_pole_factors(arg))
    return out


def _pole_glsl(expr: sp.Expr, name: str = "x") -> str:
    """GLSL for the product of the expression's pole factors (``1.0`` when there are none).

    The implicit renderer evaluates it at the pixel and at its normal probes; a sign change
    between them means the chord crossed a singularity, not a continuous piece of the level
    set. The 1-D renderer no longer uses a pole macro (the join invariant replaced it), but the
    implicit guard needs the factor itself: a simple pole is monotone across the jump, so the
    finite-difference linearity test alone can pass through it.
    """
    factors = _pole_factors(expr)
    return " * ".join(f"({func_glsl(factor, name)})" for factor in factors) if factors else "1.0"


def _domain_conditions(expr: sp.Expr) -> list[sp.Expr]:
    """Collect domain conditions (sympy expressions; each must hold identically to be
    inside the domain).

    Covers the common domain boundaries: denominator != 0, log argument > 0, sqrt argument
    >= 0, asin/acos argument ∈ [-1,1], cos != 0 for tan/sec, sin != 0 for cot/csc.
    """
    out: list[sp.Expr] = []
    if isinstance(expr, sp.Pow) and expr.exp.is_number:
        rational = as_rational(expr.exp)
        if rational is not None:
            p_expr, q_expr = rational.as_numer_denom()
            p, q = int(p_expr), int(q_expr)
            if q % 2 == 0:
                # Even root: the real branch is nonnegative, and a negative power also
                # excludes zero.
                out.append(sp.Gt(expr.base, 0) if p < 0 else sp.Ge(expr.base, 0))
            elif p < 0:
                out.append(sp.Ne(expr.base, 0))
            # Odd denominator with a positive numerator: real on the whole line.
        else:
            # An irrational exponent is evaluated through exp/log, so x must be positive.
            out.append(sp.Gt(expr.base, 0))
    if isinstance(expr, sp.log):
        out.append(sp.Gt(expr.args[0], 0))
    elif isinstance(expr, sp.asin | sp.acos):
        out.append(sp.And(sp.Ge(expr.args[0], -1), sp.Le(expr.args[0], 1)))
    elif isinstance(expr, sp.tan | sp.sec):
        out.append(sp.Ne(sp.cos(expr.args[0]), 0))
    elif isinstance(expr, sp.cot | sp.csc):
        out.append(sp.Ne(sp.sin(expr.args[0]), 0))
    for arg in expr.args:
        out.extend(_domain_conditions(arg))
    return out


def domain_glsl(expr: sp.Expr, name: str = "x") -> str | None:
    """Domain predicate (a GLSL relational expression). Returns None when it cannot be
    analysed."""
    conds = _domain_conditions(expr)
    if not conds:
        return None
    return " && ".join(f"({func_glsl(c, name)})" for c in conds)


def dfunc_glsl(expr: sp.Expr, name: str = "x") -> str:
    """GLSL text for f'(x): the march's slope bootstrap (the first 1-px-arc stride)."""
    return func_glsl(sp.diff(expr, sp.Symbol("x")), name)
# --------------------------------------------------------------------------
# Shader sources (fragment shader + per-pixel implicit drawing)
# --------------------------------------------------------------------------

VERTEX_SHADER = shader_source("fullscreen.vert")

# Implicit (signed-distance) drawing — one mechanism, the march:
#
#   Per pixel, start on the curve at the pixel's own column (seed; a pole or domain-edge
#   column probes just off itself) and march along the curve in both directions, one
#   *logical pixel of arc length* per step (the stride follows the estimated screen
#   slope; f'(x) bootstraps it, a halving probe finds it where f' is unusable). The
#   polyline the march forms is therefore sampled at uniform arc length: its chords
#   represent the curve to second order, and the stroke (distance to the joined
#   segments) keeps a constant width at any slope.
#
#   Three rules, each replacing a family of special cases:
#
#   1) Join invariant: a segment only ever joins two samples within JOIN screen px.
#      Continuous curves produce ~1.2 px chords at 1-px arc steps; a jump across a
#      pole — or across any discontinuity the static analysis never saw — is hundreds
#      of px, so the +inf/-inf connector is not detected and cut, it cannot be formed.
#   2) Escape rule: three consecutive steps outside the pixel's (stroke radius + 1)
#      ball without returning, and that direction is done — the curve is monotonically
#      away and cannot come back (a reversal resets the count). This is what bounds a
#      steep monotone branch (log's descent) instead of a viewport-relative gap.
#   3) Envelope band: when the march cannot get through — the step budget ran out
#      (sustained structure: sin(1/x) near its singularity), the path reversed on
#      itself twice within the window (a sub-stroke oscillation), or it kept missing
#      its own 1-px tangent prediction (aliasing) — the column is filled with the
#      measured [min,max] envelope instead. The edges come from a wider ±8-column,
#      16-point window (more samples are more likely to hit the extrema), exactly as
#      before: for sin(1/x) the band is its true ±1 envelope, where a polyline would
#      be moiré.
#
# The expression is a macro rather than a user function: one pixel evaluates the same
# x a handful of times (seed + f' + the march), and a macro is preprocessor expansion
# with no function-call semantics — exactly what is needed.

# Implicit curves: the level set F(x, y) = 0, stroked by the same first-order idea as the 1-D
# stroke, one dimension up. One evaluation of F and its two partials give the distance to the
# level set in pixels:
#
#     d = |F| / |grad F in pixels|,  cov = clamp(lineWidth/2 + 0.5 - d, 0, 1)
#
# Three guards keep that first-order idea honest, all symbol-free (they work for any
# elementary F):
#   - **domain** (sympy, injected as DOM): F is never evaluated where it is undefined — GLSL's
#     log(negative) is undefined and this driver returns finite garbage there (item 46), which
#     would otherwise paint the boundary itself. The domain clips the *curve*, not the stroke:
#     a pixel just outside the boundary is still inside the stroke band, so the distance is
#     extrapolated from the nearest in-domain point (otherwise x - log(y-1) thins to nothing as
#     the visible curve approaches y = 1).
#   - **linearisation validity**: the distance is trusted only where F varies the way the
#     gradient predicts across the stroke width. Near a boundary one probe is undefined; the
#     remaining side is compared with f ± R|grad F| instead of rejecting the pixel.
#   - **pole guard**: at a simple pole |grad F| explodes and |F|/|grad F| is a pseudo-zero
#     (measured: x - tan(y) painted the asymptote y = pi/2 + k*pi as a horizontal connector
#     once the y view was wide). A simple pole is monotone across the jump, so the
#     second-difference test can pass; a normal probe that crosses a zero of the pole factor
#     (cos(y) for tan) is rejected outright.


def implicit_shader_sources(expr: sp.Expr) -> tuple[str, str]:
    """Vertex/fragment shader sources for an implicit curve (the level set ``F(x, y) = 0``).

    Same uniform block as the 1-D shader, so the QML side and the curve model need no special
    case: only the expressions differ. The domain predicate comes from the same sympy analysis
    the 1-D path uses, printed over both symbols (F lives on two of them).
    """
    conds = _domain_conditions(expr)
    dom = " && ".join(f"({func_glsl(c)})" for c in conds) if conds else "true"
    return (VERTEX_SHADER, render(
        "implicit.frag",
        FUNC=func_glsl(expr, "x"),
        DFUNCX=func_glsl(sp.diff(expr, sp.Symbol("x")), "x"),
        DFUNCY=func_glsl(sp.diff(expr, sp.Symbol("y")), "x"),
        DOMXY=dom,
        POLEXY=_pole_glsl(expr),
    ))
