"""Model layer: expression -> GLSL and the shader source templates.

This layer imports no Qt and can be tested standalone without a GUI; both the camera
(camera.py) and the View (the QRhi pass) build on it.

The drawing model is **implicit (signed-distance)**: the fragment shader computes, per
pixel, the first-order screen-space distance "to the curve", sampling it by **marching
along the curve at uniform screen-space arc length** (so steep parts keep their width
and a pole/discontinuity connector can never be formed — see the comment above
``shaders/oned_sampled.frag``). Cost per frame is proportional to the pixel count and independent
of function frequency (oscillatory functions cannot drag the frame rate down through
aliasing): a column the march cannot resolve is filled as a [min,max] envelope band.
"""

from __future__ import annotations

import re

import sympy as sp
from sympy.printing.glsl import GLSLPrinter
from sympy.printing.precedence import PRECEDENCE

from .shaderbuild import render, shader_source
from .interval import (
    as_rational,
)

__all__ = [
    "VERTEX_SHADER",
    "dfunc_glsl",
    "func_glsl",
    "implicit_shader_sources",
    "shader_sources",
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


def _non_negative(expr: sp.Expr) -> bool:
    """A conservative test for ``f(x) >= 0`` everywhere it is defined.

    Used only for the undersampled wrapped-pole fill: when the range floor is known, the
    attained half-plane can be filled uniformly instead of leaving a column-phase moiré.
    """
    if expr.is_number:
        try:
            return float(expr) >= 0.0
        except (TypeError, ValueError):  # pragma: no cover - symbolic number
            return False
    if isinstance(expr, sp.exp | sp.Abs | sp.cosh):
        return True
    if isinstance(expr, sp.Pow):
        exponent = expr.exp
        if exponent.is_Integer and int(exponent) % 2 == 0:
            return True
        return as_rational(exponent) == sp.Rational(1, 2)   # sqrt
    if isinstance(expr, sp.Mul | sp.Add):
        return all(_non_negative(arg) for arg in expr.args)
    return False


def _pole_spacing(expr: sp.Expr) -> float:
    """The distance between consecutive zeros of the pole factor (world x units).

    ``tan``'s factor is ``cos(x)``: its period is ``2*pi`` and its zeros are ``pi`` apart.
    A column at least that wide contains a full pole-to-pole interval, so the inner function
    attains every real value and a wrapped pole (``exp(tan(x))``) has a connected image, not
    a gap. A non-periodic factor (``x`` for ``1/x``) has a single zero; 0 means "never dense".
    """
    spacings: list[float] = []
    for factor in _pole_factors(expr):
        period = sp.periodicity(factor, sp.Symbol("x"))
        if period is None:
            spacings.append(0.0)
            continue
        try:
            spacings.append(float(period) / 2.0)
        except (TypeError, ValueError):  # pragma: no cover - exotic period
            spacings.append(0.0)
    return min(spacings) if spacings else 0.0


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


def _domain_is_pole_only(expr: sp.Expr) -> bool:
    """True when every domain condition is just "denominator != 0" (tan, sec, 1/x, …).

    Those functions are defined everywhere except at the poles, where the value itself
    blows up and the NaN/inf test already rejects the sample. Evaluating the predicate
    again for every sample is then pure overhead.
    """
    return all(isinstance(cond, sp.Ne) and cond.rhs == 0 for cond in _domain_conditions(expr))


def unbounded_edges(expr: sp.Expr, name: str = "x") -> list[tuple[float, bool, bool]]:
    """Points on a domain boundary where the function genuinely tends to ±∞:
    [(boundary x, tends to -∞?, tends to +∞?)].

    Only **slow divergence** needs this: log(x) tends to -∞ as x→0+, but a fixed-width
    sampling window can never sample deep enough, so the curve vanishes entirely in a deep
    viewport. 1/x and tan diverge fast; their sampled values naturally exceed any viewport,
    so they need no ray (and tan's pole set is infinite, hence not enumerable analytically
    either).
    """
    sym = sp.Symbol(name)
    poles = _pole_factors(expr)
    out: list[tuple[float, bool, bool]] = []
    for cond in _domain_conditions(expr):
        lhs = cond.lhs if isinstance(cond, (sp.Gt, sp.Ge, sp.Ne)) else None
        if lhs is None or sym not in lhs.free_symbols:
            continue
        # Only polynomial boundaries (x=0, x-1=0, …) are analysed. Non-polynomial ones
        # (cos(x)=0 for tan) cost ~94ms with solve+limit, and those functions diverge fast
        # so sampling is naturally deep enough without this ray.
        if not lhs.is_polynomial(sym):
            continue
        try:
            points = sp.solve(sp.Eq(lhs, 0), sym)
        except Exception:  # noqa: BLE001 — give up if it can't be solved
            continue
        for point in points:
            if not point.is_number:
                continue
            # A boundary that is also a *pole* (a sign-changing denominator vanishes there)
            # needs no ray: its samples already exceed any viewport, and a ray would draw the
            # very connector the pole rules remove. This is the difference the module docstring
            # always claimed ("1/x and tan diverge fast") — it held for tan only because its
            # boundary is non-polynomial, while `1/x`'s x=0 boundary is polynomial and got a
            # ray, which drew a pale line across the viewport (measured at x = ±0.014).
            if any(sp.simplify(f.subs(sym, point)) == 0 for f in poles):
                continue
            down = up = False
            for side in ("+", "-"):
                try:
                    lim = sp.limit(expr, sym, point, side)
                except Exception:  # noqa: BLE001
                    continue
                if lim is sp.oo:
                    up = True
                elif lim is -sp.oo:
                    down = True
            if down or up:
                out.append((float(point), down, up))
    return sorted(set(out))


def _edge_glsl(expr: sp.Expr) -> tuple[str, str]:
    """Generate two GLSL fragments: the boundary-hit query and the boundary-direction query
    (both empty when there is no boundary)."""
    edges = unbounded_edges(expr)
    if not edges:
        return "", ""
    hit = "\n".join(
        f"    if ((xa <= {x!r}) && ({x!r} <= xb)"
        f" && abs({x!r} - 0.5 * (xa + xb)) < abs(best - 0.5 * (xa + xb))) best = {x!r};"
        for x, _d, _u in edges
    )
    du = "\n".join(
        f"    if (abs(ex - {x!r}) < 1e-9) return vec2({-1.0 if d else 0.0}, {1.0 if u else 0.0});"
        for x, d, u in edges
    )
    return hit, du



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

def shader_sources(expr: sp.Expr) -> tuple[str, str]:
    """Vertex/fragment sources for the sampled fallback.

    The interval renderer runs only in the compute path (`rhi.path_compute_supported`).
    This fragment is GGB's classic curve-plotter counterpart, used when the interval path
    rejects the expression: unsupported operations (floor/sign/Min/Max/Piecewise) or a
    dependent quotient. GGB's own `IntervalFunctionSupport` rejects both classes too, so the
    interval path never sees them.
    """
    # f'(x) is the march's slope bootstrap, an optimization rather than a requirement:
    # an unprintable derivative (Derivative(floor(x)) and friends) must not kill the
    # curve. A NaN bootstrap sends the march down its probing path instead.
    try:
        dfunc = dfunc_glsl(expr)
    except Exception:  # noqa: BLE001 — the printer raises its own error types
        dfunc = "0.0 / 0.0"
    edge_hit, edge_du = _edge_glsl(expr)
    # Domain: the macro argument is parenthesized, same reasoning as F (macros are
    # text substitution).
    dom = domain_glsl(expr, "u")
    frag = render(
        "oned_sampled.frag",
        FUNC=func_glsl(expr, "u", wrap=True),
        DFUNC=dfunc,
        EDGEHIT=edge_hit,
        EDGEDU=edge_du,
        DOM=f"#define DOM(u) ({dom})" if dom else "#define DOM(u) true",
        DOMS="#define DOMS(u) true" if _domain_is_pole_only(expr) else "#define DOMS(u) DOM(u)",
    )
    return (VERTEX_SHADER, frag)
