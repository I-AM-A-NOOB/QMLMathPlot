"""Model layer: expression -> GLSL and the shader source templates.

This layer imports no Qt and can be tested standalone without a GUI; both the camera
(camera.py) and the View (the QML ShaderEffect) build on it.

The drawing model is **implicit (signed-distance)**: the fragment shader computes, per
pixel, the first-order screen-space distance "to the curve", sampling it by **marching
along the curve at uniform screen-space arc length** (so steep parts keep their width
and a pole/discontinuity connector can never be formed — see the comment above
FRAGMENT_TEMPLATE). Cost per frame is proportional to the pixel count and independent
of function frequency (oscillatory functions cannot drag the frame rate down through
aliasing): a column the march cannot resolve is filled as a [min,max] envelope band.
"""

from __future__ import annotations

import re

import sympy as sp
from sympy.printing.glsl import GLSLPrinter
from sympy.printing.precedence import PRECEDENCE

from .interval import (
    INTERVAL_LIB,
    Unsupported,
    as_rational,
    has_dependent_quotient,
    has_sign_pole,
    interval_glsl,
)

__all__ = [
    "FRAGMENT_TEMPLATE",
    "IMPLICIT_FRAGMENT_TEMPLATE",
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

VERTEX_SHADER = """#version 440
layout(location = 0) in vec4 qt_Vertex;
layout(location = 1) in vec2 qt_MultiTexCoord0;
layout(location = 0) out vec2 vUV;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
};
void main() {
    vUV = qt_MultiTexCoord0;
    gl_Position = qt_Matrix * qt_Vertex;
}
"""

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
FRAGMENT_TEMPLATE = """#version 440
layout(location = 0) in vec2 vUV;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 camera;      // centre.x, centre.y, world units per logical pixel (x, y)
    vec2 size;        // viewport size (logical pixels)
    float lineWidth;  // line width (logical pixels)
    vec4 color;       // non-premultiplied rgba
};

#define USE_INTERVAL @USE_INTERVAL@
#define HAS_SIGN_POLE @HAS_SIGN_POLE@
#define FILL_WHOLE @HAS_SIGN_POLE@
#define POLE(u) (@POLE@)
#define HAS_DEPENDENT_QUOTIENT @HAS_DEPENDENT_QUOTIENT@

// Argument reduction: the GPU's built-in sin/cos are unreliable for large arguments;
// fold into [0,2π) before calling
#define TAU 6.283185307179586
float _wrap(float t) { return t - TAU * floor(t * (1.0 / TAU)); }
#define SIN(t) sin(_wrap(t))
#define COS(t) cos(_wrap(t))

// Point-to-segment distance (screen space). Stroking by segment => constant width
// throughout.
float _seg_dist(vec2 p, vec2 a, vec2 b) {
    vec2 ab = b - a;
    float t = clamp(dot(p - a, ab) / max(dot(ab, ab), 1e-12), 0.0, 1.0);
    return length(p - (a + t * ab));
}

// GLSL pow() is undefined for a negative base; odd roots are real there (x**(1/3)).
float _real_pow(float v, float p) {
    return v < 0.0 ? -pow(-v, p) : pow(v, p);
}

#if USE_INTERVAL
// The interval backend: one enclosure per pixel column (GeoGebra's column idea, on the GPU).
// The renderer keeps the per-pixel march for the stroke; this supplies the exact envelope
// band, the domain, and the pole topology, and replaces both the sampled ±8-column window and
// the boundary ray.
@INTERVAL_LIB@
@INTERVAL_FN@
#else
// Unbounded domain boundaries (injected from sympy one-sided limits): slowly diverging
// boundaries such as log(x) as x→0+. A fixed-width sampling window cannot sample deep
// enough, so the curve would vanish entirely in a deep viewport; here the polyline is
// extended along the boundary as a vertical ray to ±1e12, which reaches any depth and
// lies exactly on the boundary x.
float _edge_hit(float xa, float xb) {
    float best = 1e30;
@EDGEHIT@
    return best;
}
vec2 _edge_du(float ex) {
@EDGEDU@
    return vec2(0.0, 0.0);
}

// Distance (logical px) from a pixel to the boundary ray: the polyline's infinite extension
// along a slowly diverging domain boundary (log(x) as x→0+), which a fixed-width sampling
// window can never reach. nl/nh are the column's extremes — its own valid samples, or the
// probe taken just inside the domain when the column has none (see main). The ray is drawn
// only when those samples have *not* already reached that viewport edge: if the polyline
// already leaves the viewport it covers the visible part on its own, and extending it would
// be a connector. Two separate edges: `nl` is the samples' deepest point — it decides
// *whether* to draw — and `rtop` is where the down-ray starts — the descent carries every
// value below it to the boundary. (Conflating them draws a second wall next to the visible
// curve: measured as a fork at log(1+x)'s descent, x = −1 next to x = −0.996.)
float _ray_dist(vec2 p, float ex, vec2 du, float nl, float nh, float rtop,
                float y_top, float y_bot, float sx, float sy) {
    float d = 1e30;
    if (ex >= 1e29 || nl >= 1e29) {
        return d;
    }
    if (du.x != 0.0 && nl > y_bot) {
        d = min(d, _seg_dist(p, vec2(ex * sx, rtop * sy), vec2(ex * sx, -1e12)));
    }
    if (du.y != 0.0 && nh > -1e29 && nh < y_top) {
        d = min(d, _seg_dist(p, vec2(ex * sx, nh * sy), vec2(ex * sx, 1e12)));
    }
    return d;
}
#endif

#define F(u) (@FUNC@)
#define DF @DFUNC@      // f'(x): the march's slope bootstrap (NaN/garbage => probe instead)
// Domain predicate (pixels outside the domain are not drawn). DOMS is the per-sample
// variant: for a purely pole-shaped domain (tan, 1/x, sec, …) it is `true`, because
// there the NaN/inf test on the value already rejects out-of-domain samples.
@DOM@
@DOMS@
// The march's knobs: steps per direction (the budget — sustained structure exhausts
// it) and the join invariant's radius (a segment only ever joins samples this close).
#define MARCH 12
#define JOIN 2.5

void main() {
    // Camera -> world: the centre lands in the middle of the item, one logical pixel is
    // (camera.z, camera.w) world units, and vUV.y is top-down (screen y grows downwards).
    float x = camera.x + (vUV.x - 0.5) * size.x * camera.z;
    float y = camera.y + (0.5 - vUV.y) * size.y * camera.w;

    float R = lineWidth * 0.5 + 0.5;   // stroke radius + AA ramp (logical px)
    float BW = max(R - 0.75, 0.5);      // band feather: a filled edge, not a stroked one
    float spany = size.y * camera.w;   // visible y span (world units)
    float dx = camera.z;               // world x per pixel
    float sx = 1.0 / camera.z;         // pixels per world x
    float sy = 1.0 / camera.w;         // pixels per world y
    float y_top = camera.y + 0.5 * spany;
    float y_bot = camera.y - 0.5 * spany;

#if USE_INTERVAL
    // Conservative reject: every sample/segment used below lies within ±2 columns, so the
    // interval over that window bounds the curve's y values there. A pixel farther than the
    // stroke radius from that range cannot be painted; skip the 14 F evaluations and the
    // column/stroke-footprint intervals. This is the cheap analogue of a column cache: one
    // interval evaluation instead of the whole per-pixel sample loop. Whole/infinite columns
    // do not reject (the envelope may fill the viewport); empty columns have no curve at all.
    IVal wide = FI(x - 2.0 * dx, x + 2.0 * dx);
    if (wide.kind == IK_EMPTY) {
        fragColor = vec4(0.0);
        return;
    }
    if (wide.kind == IK_CONNECTED && wide.lo > -IINF && wide.hi < IINF) {
        float pad = R * camera.w;
        if (y < wide.lo - pad || y > wide.hi + pad) {
            fragColor = vec4(0.0);
            return;
        }
    }

    IVal own = FI(x - 0.5 * dx, x + 0.5 * dx);
    IVal col = own;
    if (col.kind == IK_EMPTY) {
        // The stroke's outer half may cross the domain boundary by up to one radius. Only a
        // column with no in-domain value needs this wider footprint; skipping it for every
        // other pixel removes one interval evaluation from the common path.
        IVal gate = FI(x - R * dx, x + R * dx);
        if (gate.kind != IK_EMPTY) {
            col = gate;
        }
    }

    // A pole inside this column is a jump, even when a monotone map such as exp()
    // turns the two one-sided limits into a connected range (exp(tan(x)): 0 and +inf).
    // The interval paths below would bridge that jump with a vertical connector.
    bool pole_col = HAS_SIGN_POLE && POLE(x - 0.5 * dx) * POLE(x + 0.5 * dx) <= 0.0;

    // ---- fixed-x sub-pixel polyline (GeoGebra's sampled polyline, on the GPU) ----
    // 14 samples over ±2 columns (0.286-column spacing) cover the perpendicular foot even at
    // 89°: a ±1-column window lost 42 % of the width at 84°. Joining the samples gives a
    // polyline whose segments are diagonal, so the stroke is smooth at any zoom. The interval
    // band stays separate (below); it no longer has to be inferred from this window.
    const int NS = 14;
    float smp[NS];
    float smpx[NS];
    bool ok[NS];
    float sturns = 0.0;       // reversals inside this pixel's own column only
    float pmin = 1e30;
    float prev_s = 0.0;
    float prev_x = 0.0;
    float prev_t = 0.0;
    bool prev_ok = false;
    float prev_d = 0.0;
    bool prev_d_ok = false;
    for (int i = 0; i < NS; i++) {
        float t = -2.0 + (float(i) + 0.5) * (4.0 / float(NS));
        float xi = x + t * dx;
        float v = 1e30;
        if (DOMS(xi)) {
            v = F(xi);
        }
        smp[i] = v;
        smpx[i] = xi;
        ok[i] = (v == v) && (abs(v) < 1e30);
        if (ok[i]) {
            if (prev_ok) {
                bool skip = (col.kind == IK_WHOLE || col.kind == IK_INVERTED)
                        || (HAS_SIGN_POLE && POLE(prev_x) * POLE(xi) <= 0.0)
                        || (HAS_DEPENDENT_QUOTIENT && abs(v - prev_s) * sy > JOIN);
                if (!skip) {
                    pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
                        vec2(prev_x * sx, prev_s * sy), vec2(xi * sx, v * sy)));
                }
                float d = v - prev_s;
                if (abs(t) <= 0.5 && abs(prev_t) <= 0.5
                        && prev_d_ok && prev_d * d < 0.0
                        && abs(prev_d) > 1e-4 && abs(d) > 1e-4) {
                    sturns += 1.0;
                }
                prev_d = d;
                prev_d_ok = true;
            } else {
                prev_d_ok = false;
            }
            prev_s = v;
            prev_x = xi;
            prev_ok = true;
        } else {
            prev_ok = false;
            prev_d_ok = false;
        }
    }

    // GeoGebra's unbounded-column rule (IntervalPath.drawNormalInfinity): only when the
    // finite bound itself is on screen. The segment uses the column's left edge and middle.
    if (!pole_col && col.kind == IK_CONNECTED && col.lo <= -IINF
            && col.hi >= y_bot && col.hi <= y_top) {
        pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
            vec2((x - 0.5 * dx) * sx, col.hi * sy), vec2(x * sx, y_bot * sy)));
    }
    if (!pole_col && col.kind == IK_CONNECTED && col.hi >= IINF
            && col.lo >= y_bot && col.lo <= y_top) {
        pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
            vec2((x - 0.5 * dx) * sx, col.lo * sy), vec2(x * sx, y_top * sy)));
    }
    // The deep log case: the finite bound is *above* the viewport but the column's interval
    // covers it (the visible curve lies inside this x column). GeoGebra's normal-infinity
    // branch declines to draw here; the column is effectively whole (drawWhole), so a
    // vertical segment at the left edge is GGB's own rule for that topology.
    if (!pole_col && col.kind == IK_CONNECTED && col.lo <= -IINF && col.hi > y_top) {
        pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
            vec2((x - 0.5 * dx) * sx, y_bot * sy), vec2((x - 0.5 * dx) * sx, y_top * sy)));
    }
    if (!pole_col && col.kind == IK_CONNECTED && col.hi >= IINF && col.lo < y_bot) {
        pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
            vec2((x - 0.5 * dx) * sx, y_bot * sy), vec2((x - 0.5 * dx) * sx, y_top * sy)));
    }

    // GeoGebra's DrawInvertedInterval: an inverted column draws the two attained rays as
    // vertical segments at the column's *outer* edges — top at x.high, bottom at x.low —
    // not as a filled band. (The joined variant, JoinLines.inverted, adds the neighbour
    // joins; the sample polyline already draws those branches.)
    if (col.kind == IK_INVERTED) {
        if (col.lo < y_top) {
            pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
                vec2((x + 0.5 * dx) * sx, y_top * sy),
                vec2((x + 0.5 * dx) * sx, col.hi * sy)));
            pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
                vec2(x * sx, y_top * sy), vec2(x * sx, col.hi * sy)));
        }
        if (col.hi > y_bot) {
            pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
                vec2((x - 0.5 * dx) * sx, y_bot * sy),
                vec2((x - 0.5 * dx) * sx, col.lo * sy)));
            pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
                vec2(x * sx, y_bot * sy), vec2(x * sx, col.lo * sy)));
        }
    }

    // GeoGebra's DrawInterval.drawWhole: one vertical segment at the column's left edge.
    if (col.kind == IK_WHOLE && FILL_WHOLE) {
        pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
            vec2((x - 0.5 * dx) * sx, y_bot * sy), vec2((x - 0.5 * dx) * sx, y_top * sy)));
        pmin = min(pmin, _seg_dist(vec2(x * sx, y * sy),
            vec2(x * sx, y_bot * sy), vec2(x * sx, y_top * sy)));
    }

    float band_cov = 0.0;
    if (!pole_col && col.kind == IK_CONNECTED) {
        // GeoGebra's DrawInterval path for a sub-column oscillation: the current column's
        // joined diagonal plus the vertical join at x.low (drawUp/drawDown compare the high
        // edges). An independent column is a vertical at x.high. This replaces the old
        // filled rectangle: the interval path is a stroked line, not a fill.
        float fill = smoothstep(1.5, 3.0, sturns);
        if (fill > 0.0 && !(HAS_DEPENDENT_QUOTIENT && !FILL_WHOLE)) {
            IVal prevc = FI(x - 1.5 * dx, x - 0.5 * dx);
            float gcov = 0.0;
            if (prevc.kind == IK_CONNECTED && prevc.lo > -IINF && prevc.hi < IINF) {
                if (col.hi > prevc.hi) {
                    gcov = max(gcov, clamp(R - _seg_dist(vec2(x * sx, y * sy),
                        vec2((x - 0.5 * dx) * sx, prevc.hi * sy),
                        vec2((x - 0.5 * dx) * sx, col.lo * sy)), 0.0, 1.0));
                    gcov = max(gcov, clamp(R - _seg_dist(vec2(x * sx, y * sy),
                        vec2((x - 0.5 * dx) * sx, col.lo * sy),
                        vec2((x + 0.5 * dx) * sx, col.hi * sy)), 0.0, 1.0));
                } else {
                    gcov = max(gcov, clamp(R - _seg_dist(vec2(x * sx, y * sy),
                        vec2((x - 0.5 * dx) * sx, prevc.lo * sy),
                        vec2((x - 0.5 * dx) * sx, col.hi * sy)), 0.0, 1.0));
                    gcov = max(gcov, clamp(R - _seg_dist(vec2(x * sx, y * sy),
                        vec2((x - 0.5 * dx) * sx, col.hi * sy),
                        vec2((x + 0.5 * dx) * sx, col.lo * sy)), 0.0, 1.0));
                }
            } else {
                gcov = clamp(R - _seg_dist(vec2(x * sx, y * sy),
                    vec2((x + 0.5 * dx) * sx, col.lo * sy),
                    vec2((x + 0.5 * dx) * sx, col.hi * sy)), 0.0, 1.0);
            }
            band_cov = fill * gcov;
        }
    }
    if (HAS_DEPENDENT_QUOTIENT && !FILL_WHOLE) {
        band_cov = 0.0;   // dependency overestimation: the polyline draws the real branches
    }
    float cov = max(band_cov, clamp(R - pmin, 0.0, 1.0));
    if (!DOM(x)) {
        float a = cov * color.a * qt_Opacity;
        fragColor = vec4(color.rgb * a, a);
        return;
    }
#else
    // Legacy path (unsupported operations): the sampled envelope band plus the boundary ray.
    // Pixels outside the domain are still inside the curve's stroke band, so the ray/chords
    // below paint the half that the domain test would otherwise discard.
    // The boundary query is the sampled interval **widened by the stroke radius**
    // (lineWidth/2 + 0.5): the coverage ramp reaches that far, so a narrower window clips the
    // ray's own stroke (measured: the ray was evaluated only by the ±1-column band of pixels
    // and the descent's neighbours got distance to an endpoint — 1.00 device px instead of
    // 3.00). The constants are the sample offsets of the loop below (-1 + 0.25/4, -1 + 7.25/4).
    float edge_r = (lineWidth * 0.5 + 0.5) * dx;   // stroke radius, world units
    float ex_ = _edge_hit(x - 0.9375 * dx - edge_r, x + 0.8125 * dx + edge_r);
    vec2 du_ = (ex_ < 1e29) ? _edge_du(ex_) : vec2(0.0, 0.0);
    // A column with no valid sample (a pixel just outside the domain) still lies inside the
    // stroke's band around the boundary, so the band must be evaluated there too — but its
    // extent has to come from the function itself, probed just inside the domain, since every
    // sample of such a column is out of domain. Judging it by the sentinel alone drew a
    // full-height line at `log(x-1)`'s boundary in a shallow view (where the visible curve is
    // nowhere near the boundary), while ignoring those pixels altogether cost the descent its
    // whole outer half (1.00-1.67 of the nominal 3.00 device px) in a deep one.
    float edge_nl = 1e30;
    float edge_nh = -1e30;
    float edge_top = 1e30;   // the down-ray's top edge (the gate is edge_nl, not this)
    if (ex_ < 1e29) {
        // Probe a short ladder just inside the boundary on whichever side is in the
        // domain: the down-ray starts at the HIGHEST probe (the topmost value the
        // descent carries to the boundary), not the deepest — a single close probe
        // starts the ray too low and leaves a hole between it and the polyline's own
        // reach (measured: a 6-row gap at the foot of log(1+x)'s wall). The
        // shallow-boundary regression the probe exists for is preserved: with every
        // probe below the viewport bottom the ray still declines to draw (log(x-1) in
        // a shallow view stays clean), and a boundary with no in-domain probe at all
        // gets neither ray (the sentinels fail both `nl > y_bot` and `nh < y_top`).
        float best_down = -1e30;      // the down-ray's top edge
        float best_up = 1e30;         // the deepest value (the gate), and the up-ray's
        for (int k = 0; k < 4; k++) {  // bottom edge (the up-ray starts at its shallowest)
            for (int side = 0; side < 2; side++) {
                float sgn = side == 0 ? 1.0 : -1.0;
                float xt = ex_ + sgn * (float(k) + 0.25) * dx;
                float s = 1e30;
                if (DOMS(xt)) {
                    s = F(xt);
                }
                if (s == s && abs(s) < 1e30) {
                    best_down = max(best_down, s);
                    best_up = min(best_up, s);
                }
            }
        }
        edge_nl = best_up;
        edge_nh = best_down;
        edge_top = best_down;
    }

    if (!DOM(x)) {
        // A pixel just outside the domain is still inside the curve's stroke band:
        // near a boundary that is not (yet) vertical, the curve passes within a
        // stroke width at this pixel's height — one or two pixels inside. The ray
        // alone only covers the deep, genuinely vertical part (it starts at the
        // boundary probe), which left a hole between the two (measured: log(1+x)'s
        // descent wall lost its whole middle column, rows spanning the shallow
        // part of the view, until the just-inside probe chords were added here).
        float rcov = clamp(lineWidth * 0.5 + 0.5
                          - _ray_dist(vec2(x * sx, y * sy), ex_, du_, edge_nl, edge_nh,
                                      edge_top, y_top, y_bot, sx, sy),
                          0.0, 1.0);
        if (ex_ < 1e29) {
            // Just inside the boundary: four chord samples and their segments, the
            // same consistent-pair idea as the column probe, with the midpoint test
            // replaced by "the pair brackets this pixel's height" (a monotone
            // near-boundary stretch is exactly the case that qualifies).
            float pv[4];
            bool okv[4];
            for (int i = 0; i < 4; i++) {
                float xt = ex_ + (float(i) + 0.5) * dx;
                pv[i] = 1e30;
                if (DOMS(xt)) {
                    pv[i] = F(xt);
                }
                okv[i] = (pv[i] == pv[i]) && (abs(pv[i]) < 1e30);
            }
            for (int i = 0; i < 3; i++) {
                if (okv[i] && okv[i + 1]
                        && y >= min(pv[i], pv[i + 1]) && y <= max(pv[i], pv[i + 1])) {
                    float d = _seg_dist(vec2(x * sx, y * sy),
                                        vec2((ex_ + (float(i) + 0.5) * dx) * sx, pv[i] * sy),
                                        vec2((ex_ + (float(i + 1) + 0.5) * dx) * sx, pv[i + 1] * sy));
                    rcov = max(rcov, clamp(lineWidth * 0.5 + 0.5 - d, 0.0, 1.0));
                }
            }
        }
        fragColor = vec4(color.rgb * rcov * color.a * qt_Opacity, rcov * color.a * qt_Opacity);
        return;
    }
#endif

#if !USE_INTERVAL
    // The march and the legacy band: only the unsupported-expression path still needs them.
    // The interval path is the fixed-x polyline above plus the exact column band, so it must
    // not pay for a single march evaluation.
    float f0 = F(x);
    float d1 = DF;                       // f'(x) at the pixel's own column
    bool have_m0 = (d1 == d1) && (abs(d1) < 1e30);
    float m0 = have_m0 ? d1 * camera.z / camera.w : 0.0;   // screen slope at the seed

    bool marched = false;

    // ---- the march: arc-length sampling along the curve, both directions ----
    // Seed: the curve at this pixel's own column. A pole or domain-edge column probes
    // just off itself and the nearest valid neighbour carries the march: a singularity
    // column such as sin(1/x)'s still needs its envelope band, while a plain pole
    // column of 1/x marches up its own branches and draws nothing at the pole.
    float xs = x;
    float ys = f0;
    bool have_seed = DOMS(x) && (ys == ys) && (abs(ys) < 1e30);
    if (!have_seed) {
        for (int k = 0; k < 4 && !have_seed; k++) {
            float mag = 0.25 * dx * float(k / 2 + 1);
            xs = x + ((k - (k / 2) * 2 == 0) ? mag : -mag);
            ys = 1e30;
            if (DOMS(xs)) {
                ys = F(xs);
            }
            have_seed = (ys == ys) && (abs(ys) < 1e30);
        }
    }

    vec2 pp = vec2(x * sx, y * sy);    // this pixel, screen space

    // Tangent-foot seed: on a steep curve the column's own curve point can be a
    // whole viewport away (in arc) from this pixel's foot, and no budget can walk
    // that far along the curve (measured: dashed strokes of one budget's length on
    // y = 10x). Project the pixel onto the tangent at the column seed — one dot
    // product, then one evaluation to land the seed on the curve there. This is the
    // coverage the old fixed-x renderer got from long sample-chords, which the join
    // invariant refuses by design. No cap on the projection: far along a near-vertical
    // tangent is exactly where a deep-zoomed pole's foot is (the two branches merge
    // into a wall), and a seed that lands nowhere near this pixel is filtered by the
    // hoplessness gate below, which is what the gate is for.
    if (have_seed && have_m0) {
        vec2 u = vec2(1.0, m0) / sqrt(1.0 + m0 * m0);     // unit tangent (screen px)
        float t = dot(pp - vec2(xs * sx, ys * sy), u);
        float xn2 = xs + t * u.x * dx;
        if (DOMS(xn2)) {
            float yn2 = F(xn2);
            if (yn2 == yn2 && abs(yn2) < 1e30) {
                xs = xn2;
                ys = yn2;
            }
        }
    }
    float dmin = 1e30;                 // stroke: min distance to the joined polyline
    float nl = 1e30;                   // the column's y extent (the boundary ray's start)
    float nh = -1e30;
    float turns = 0.0;                 // direction reversals along the march
    float unstable = 0.0;              // steps that missed their own tangent prediction
    bool unresolved = false;           // the budget ran out: sustained structure

    if (have_seed && distance(pp, vec2(xs * sx, ys * sy)) < R + 2.0 + float(MARCH)) {
        // The march is worth running only when the seeded curve point is within
        // walking distance of this pixel (the ball plus the budget): a seed further
        // away than that stands in for a pixel no curve point can serve, and marching
        // anyway burns the budget and fires a spurious band probe on background.
        nl = min(nl, ys);
        nh = max(nh, ys);
        for (int dir = -1; dir <= 1; dir += 2) {
            float lx = xs;             // the seed carries both directions
            float ly = ys;
            float m = m0;              // screen slope estimate (f' bootstraps it)
            bool run = have_m0;        // the slope is usable for a 1-px-arc stride
            float dprev = 1e30;        // previous step's distance from this pixel
            float pending = run ? 1.0 / sqrt(1.0 + m * m) : 0.25;   // next x stride (px)
            float pd = 0.0;            // previous joined Δy (screen) — turn detection
            float esc = 0.0;           // consecutive steps outside the pixel's R+1 ball
            int step = 0;
            for (; step < MARCH; step++) {
                float xn = lx + float(dir) * pending * dx;
                float yn = 1e30;      // out-of-domain steps stay unevaluated (the
                if (DOMS(xn)) {       // wide window's note on log(negative))
                    yn = F(xn);
                }
                if (!((yn == yn) && (abs(yn) < 1e30))) {
                    // A pole landing (measure-zero) or a domain edge. One wider retry
                    // steps over the former; a second failure is a domain edge, and
                    // this direction ends here (the boundary ray owns the far side).
                    xn = lx + float(dir) * (2.0 * pending + 0.25) * dx;
                    yn = 1e30;
                    if (DOMS(xn)) {
                        yn = F(xn);
                    }
                    if (!((yn == yn) && (abs(yn) < 1e30))) {
                        break;
                    }
                    run = false;
                    pending = 0.25;
                }
                nl = min(nl, yn);
                nh = max(nh, yn);
                vec2 pt = vec2(xn * sx, yn * sy);      // screen space
                vec2 last = vec2(lx * sx, ly * sy);
                // The join invariant: a segment only ever joins two samples within
                // JOIN px of each other. Chords of 1-px-arc steps are ~1.2 px on any
                // continuous curve; a jump across a pole — or across a plain
                // discontinuity the static analysis never saw — is hundreds of px.
                // The connector is not detected and cut, it cannot be formed.
                if (distance(pt, last) <= JOIN) {
                    dmin = min(dmin, _seg_dist(pp, last, pt));
                    float dy = pt.y - last.y;
                    if (run) {
                        // Tangent prediction: a smooth curve misses its own 1-px
                        // extrapolation by curvature only; aliasing misses by ~a px.
                        vec2 pred = last + float(dir) * vec2(1.0, m) / sqrt(1.0 + m * m);
                        if (distance(pt, pred) > 0.75) {
                            unstable += 1.0;
                        }
                        // A reversal may well come back: it resets the escape count.
                        // The noise floor matters: on a nearly-flat stretch the dy of a
                        // 1-px step is float noise (x/sqrt(x*x) moves ~1e-12 per step
                        // along a constant branch), whose sign flips read as turns and
                        // band the whole column (measured: a solid ±1 column at the
                        // function's jump).
                        if (pd * dy < 0.0 && abs(pd) > 1e-4 && abs(dy) > 1e-4) {
                            turns += 1.0;
                            esc = 0.0;
                        }
                    }
                    float xd = pt.x - last.x;
                    m = dy / (abs(xd) > 1e-12 ? xd : float(dir) * 1e-12);
                    pd = dy;
                    run = true;
                    pending = 1.0 / sqrt(1.0 + m * m);
                    lx = xn;
                    ly = yn;
                } else if (!run && pending > 0.01) {
                    // A *probe* step (establishing the slope) that cannot join: the
                    // curve is steeper than the probe assumed — halve until joinable
                    // (four halvings at most; f' bootstraps steeper than that).
                    pending *= 0.5;
                    continue;
                } else {
                    // No continuous path at an honest 1-px-arc stride (or a probe
                    // out of halvings): a discontinuity. Resume on the far branch —
                    // the join invariant has refused the connector.
                    run = false;
                    pd = 0.0;
                    pending = 0.25;
                    lx = xn;
                    ly = yn;
                }
                float dpt = distance(pt, pp);
                if (dpt > R + 1.0) {
                    // Outside the ball, but only *receding* steps count: a march
                    // that is still closing in on this pixel (a steep curve's foot
                    // is a long way down the curve from the column seed) must not
                    // be stopped on its way in.
                    esc = (dpt > dprev) ? esc + 1.0 : 0.0;
                    if (esc >= 3.0) {
                        break;         // receding for 3 steps, not coming back
                    }
                } else {
                    esc = 0.0;         // re-entered the pixel's ball (a sweep passes)
                }
                dprev = dpt;
                if (yn > y_top + (R + 1.0) * camera.w || yn < y_bot - (R + 1.0) * camera.w) {
                    break;             // past the viewport edge by the stroke radius
                }
            }
            if (step >= MARCH) {
                unresolved = true;    // the budget ran out: the march cannot get
                                     // through this in 1-px steps (sin(1/x)'s
                                     // singularity) — the envelope band's case
            }
        }
    }

#endif
    // ---- stroke ----
#if USE_INTERVAL
    // The fixed-x polyline (above) is the stroke; the interval band is the fill. No march,
    // gate, chord or neighbouring-column segment: the sample spacing is sub-pixel and the
    // segment distance gives the constant width on a steep branch.
    cov = max(cov, clamp(R - pmin, 0.0, 1.0));
#else
    // Legacy: distance to the joined polyline, the boundary ray included. A slowly diverging
    // boundary such as log(x)'s is an honest vertical asymptote, and a column with no
    // valid sample of its own borrows the probe from the ray setup above.
    float cov = clamp(R - min(dmin, _ray_dist(pp, ex_, du_, nl < 1e29 ? nl : edge_nl,
                                              nh > -1e29 ? nh : edge_nh,
                                              nh > -1e29 ? nh : edge_top,
                                              y_top, y_bot, sx, sy)), 0.0, 1.0);

    // ---- column probe: the structure a local march cannot see ----
    // A sub-column oscillation (sin(1/x) near its singularity) is *resolved* along
    // the arc — the march follows it faithfully for millions of pixels without ever
    // turning — while the column still packs whole periods between neighbouring
    // pixels. Only a fixed-x probe across the column window sees it: grid values at
    // 1-px spacing with the midpoints between them — a midpoint that falls outside
    // its pair's [min,max] is structure below the pair spacing (for independent
    // samples it lands outside 2 times out of 3). A pole landing corrupts exactly one
    // pair, which is why the trigger needs two violations; an oscillation corrupts
    // them all. The slope gate (|f'| > 1 screen unit) keeps the probe off flat
    // background — the same gate the old fixed-grid renderer used.
    float violations = 0.0;    // pairs with structure below the pair spacing
    float bracket = 0.0;       // soft window: a consistent pair that spans this pixel's y
    if (have_m0 && (abs(m0) > 1.0 || unresolved || turns >= 2.0 || unstable >= 4.0)) {
        // 1-px spacing is deliberate: the chord-stroke below needs pairs that SPAN
        // this pixel's y (a steep wall's column value sits a whole view away from
        // the pixel at hand), which only a coarse grid provides. A finer grid hugs
        // the column's own value and brackets nothing. (The very deep merged wall —
        // two branches within a stroke of each other under an enormous y view — has
        // its visible arc between the column and the pole, narrower than this step;
        // covering it needs a bisection seed towards the pole, which is a separate
        // mechanism.)
        float grid[9];
        bool okg[9];
        for (int i = 0; i < 9; i++) {
            float off = float(i) - 4.0;                  // -4 .. +4 px, 1-px spacing
            float s = 1e30;      // leave out-of-domain samples unevaluated (see the
            if (i == 4) {       // wide window's note: log(negative) is undefined, and a
                s = f0;         // discarded evaluation still perturbs the codegen)
            } else if (DOMS(x + off * dx)) {
                s = F(x + off * dx);
            }
            grid[i] = s;
            okg[i] = (s == s) && (abs(s) < 1e30);
        }
        for (int i = 0; i < 8; i++) {
            if (okg[i] && okg[i + 1]) {
                float xm = x + (float(i) - 3.5) * dx;    // the pair's midpoint
                float fm = 1e30;
                bool okm = false;
                if (DOMS(xm)) {
                    fm = F(xm);
                    okm = (fm == fm) && (abs(fm) < 1e30);
                }
                if (okm && (fm < min(grid[i], grid[i + 1])
                            || fm > max(grid[i], grid[i + 1]))) {
                    violations += 1.0;    // structure below the pair spacing
                } else if (okm && fm > min(grid[i], grid[i + 1]) && fm < max(grid[i], grid[i + 1])) {
                    // A consistent pair — its midpoint strictly inside its span — is a
                    // trustworthy chord: the connected curve between the pair's ends
                    // really does pass through the values in between (the jump pair
                    // of a pole/jump column just failed the midpoint test above, and
                    // a flat or degenerate span brackets nothing). Stroke those
                    // chords: it is the geometry that covers a steep monotone wall
                    // (1/x, tan at large |y|), whose foot is a whole view up the arc
                    // where no march budget can walk. The chords stay one stride
                    // long, so this is the old fixed-x renderer's coverage without
                    // its connector: the straddling pair never qualifies.
                    float dchord = _seg_dist(pp,
                        vec2((x + (float(i) - 4.0) * dx) * sx, grid[i] * sy),
                        vec2((x + (float(i + 1) - 4.0) * dx) * sx, grid[i + 1] * sy));
                    bracket = max(bracket, clamp(R - dchord, 0.0, 1.0));
                }
            }
        }
    }

    // ---- envelope band: the columns the march could not resolve ----
    // The budget ran out (sustained structure), the path reversed on itself twice
    // within the window (a sub-stroke oscillation), it kept missing its own tangent
    // prediction (aliasing), or the column probe found structure below the pair
    // spacing (a sub-column oscillation): fill the measured envelope instead. The
    // edges come from a wider ±8-column, 16-point window — more samples are more
    // likely to hit the extrema, so the edges do not bite dark seams/steps (measured:
    // 2.4 px between neighbouring columns once this was added).
    // Every trigger's weight tapers with its count, so the region's edge is feathered
    // rather than a hard cut — and the deliberate 2-px overshoot of the fill's ramp
    // (the comb-smoothing trade-off) stays feathered there (measured: 45 solid pixels
    // beyond ±1 for the old renderer, of a 50 budget). The exhausted budget is a
    // binary, all-or-nothing signal: full weight.
    // Everything is hoisted out of the trigger's `if` on purpose: scoped variants let
    // the optimiser re-derive the coverage differently per source edit, and a shader
    // whose output changes under an unrelated edit is a shader no one can debug.
    float band_w = unresolved ? 1.0
        : max(max(bracket, max(smoothstep(2.0, 4.0, turns),
                               smoothstep(4.0, 6.0, unstable))),
              smoothstep(2.0, 4.0, violations));
    float wl = 1e30;
    float wh = -1e30;
    // Same construction as the column probe on purpose — same inline expression, same
    // inline guards: a `t`-indexed variant of this window computed out-of-envelope
    // samples under this driver's optimiser (measured: wh−wl came out 4+ world units
    // where the true spread was 3.2, and the band collapsed to zero).
    for (int i = 0; i < 17; i++) {
        float off = float(i) - 8.0;                     // -8 .. +8 px, 1-px spacing
        float s = 1e30;
        if (DOMS(x + off * dx)) {
            s = F(x + off * dx);
        }
        if (s == s && abs(s) < 1e30) {
            wl = min(wl, s);
            wh = max(wh, s);
        }
    }
    if (band_w > 0.0) {
        // The 1.5-px feather (down from 2 in the fixed-grid renderer, whose band
        // region was ~18 % narrower): enough to smooth the column-to-column edges,
        // and the overshoot beyond the envelope stays under the solid threshold.
        float band = clamp((wh - y) * sy + 1.5, 0.0, 1.0)
                   * clamp((y - wl) * sy + 1.5, 0.0, 1.0);
        // The dense-chaos gate: a pole column's or a jump column's envelope spans
        // the whole viewport without taking none of those values (the window's samples
        // form a monotone "V" through the jump — exactly one violating pair), while
        // a genuinely oversampled oscillation violates most of its pairs. The count
        // is the gate (the old renderer's 32×-viewport gap, without recognising the
        // pole — and unlike a world-unit gate it does not drift with zoom). The
        // smoothstep on the envelope span keeps a band narrower than the stroke
        // itself pointless: the polyline already covers it — a resolvable extremum
        // must stay a stroke, not become a block.
        cov = max(cov, band * band_w * smoothstep(1.5, 4.0, (wh - wl) * sy)
                            * max(smoothstep(3.0, 6.0, violations), bracket));
    }
#endif

    float a = cov * color.a * qt_Opacity;
#if !USE_INTERVAL
    if (isnan(f0) || isinf(f0)) {
        a = 0.0;                       // discard points like sin(1/0), 1/0 outright
    }
#endif
    fragColor = vec4(color.rgb * a, a);   // premultiplied alpha
}
"""


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
IMPLICIT_FRAGMENT_TEMPLATE = """#version 440
layout(location = 0) in vec2 vUV;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 camera;      // centre.x, centre.y, world units per logical pixel (x, y)
    vec2 size;        // viewport size (logical pixels)
    float lineWidth;  // line width (logical pixels)
    vec4 color;       // non-premultiplied rgba
};

// Argument reduction: the GPU's built-in sin/cos are unreliable for large arguments;
// fold into [0,2π) before calling
#define TAU 6.283185307179586
float _wrap(float t) { return t - TAU * floor(t * (1.0 / TAU)); }
#define SIN(t) sin(_wrap(t))
#define COS(t) cos(_wrap(t))

// GLSL pow() is undefined for a negative base; odd roots are real there (x**(1/3)).
float _real_pow(float v, float p) {
    return v < 0.0 ? -pow(-v, p) : pow(v, p);
}

// Point-to-segment distance in logical pixels: the marching cell's contour segment is
// stroked by the same rule as the 1-D polyline.
float _seg_dist(vec2 p, vec2 a, vec2 b) {
    vec2 ab = b - a;
    float t = clamp(dot(p - a, ab) / max(dot(ab, ab), 1e-12), 0.0, 1.0);
    return length(p - (a + t * ab));
}

// F is a macro with two parameters (text substitution; the call always passes the plain pixel
// coordinates, so no extra parentheses are needed on the arguments — see the note in the 1-D
// template). FX/FY are function-like too, because the one-sided boundary extension evaluates the
// gradient at a nearby in-domain point. A partial that is not a single term, e.g.
// d/dx (x-1)^2 = 2*x - 2, is wrapped so it can stand inside a product.
#define F(x, y) (@FUNC@)
#define FX(x, y) (@DFUNCX@)
#define FY(x, y) (@DFUNCY@)
#define DOM(x, y) (@DOMXY@)
// Product of the expression's pole factors (cos(y) for tan(y), a denominator for 1/(...)).
// A probe that crosses a zero of this product crossed a singularity, not a continuous chord.
#define POLE(x, y) (@POLEXY@)

void main() {
    float x = camera.x + (vUV.x - 0.5) * size.x * camera.z;
    float y = camera.y + (0.5 - vUV.y) * size.y * camera.w;
    float R = lineWidth * 0.5 + 0.5;

    bool dom = DOM(x, y);
    float f = 0.0;
    float gx = 0.0;
    float gy = 0.0;
    if (dom) {
        f = F(x, y);
        // |grad F| in pixels: the partial per world unit times the world units per pixel
        gx = FX(x, y) * camera.z;
        gy = FY(x, y) * camera.w;
    }

    // Distance to the level set, in pixels. Outside the domain the level set is still a
    // geometric object: a stroke is a band around it, so the domain clips the *curve*, not the
    // band. Without this, x - log(y-1) loses its lower half as the curve approaches y = 1
    // (the visible curve sits within one stroke radius of the boundary), and y - log(x) does
    // the same as x -> 0+. Evaluate the first-order model at the nearest in-domain point and
    // extrapolate it to this pixel.
    float d = 1e30;
    if (dom) {
        float gnorm = max(sqrt(gx * gx + gy * gy), 1e-9);
        d = abs(f) / gnorm;
    } else {
        for (int i = 0; i < 8; i++) {
            float a = float(i) * 0.7853981634;
            float ux = cos(a);
            float uy = sin(a);
            for (int k = 1; k <= 3; k++) {
                float rr = R * 0.5 * float(k);
                float qx = x + ux * rr * camera.z;
                float qy = y + uy * rr * camera.w;
                if (DOM(qx, qy)) {
                    float qf = F(qx, qy);
                    float qfx = FX(qx, qy);
                    float qfy = FY(qx, qy);
                    bool okq = (qf == qf) && (abs(qf) < 1e30)
                            && (qfx == qfx) && (qfy == qfy);
                    if (okq && qfx * qfx + qfy * qfy > 1e-18) {
                        // Project q onto the level set and measure the distance from p to
                        // that point. Extrapolating q's linear model to p instead is wrong
                        // across a boundary: log's model is only local, and far out it folds
                        // back onto the visible branch as a fake line.
                        float sx = qx;
                        float sy = qy;
                        for (int it = 0; it < 2; it++) {
                            float ff = F(sx, sy);
                            float fx = FX(sx, sy);
                            float fy = FY(sx, sy);
                            float den = fx * fx + fy * fy;
                            if (den > 1e-18) {
                                float tx = sx - ff * fx / den;
                                float ty = sy - ff * fy / den;
                                // Damp a step that leaves the domain: near log's boundary
                                // the full Newton step overshoots into x < 0, although the
                                // root is a fraction of a pixel inside it.
                                for (int h = 0; h < 4 && !DOM(tx, ty); h++) {
                                    tx = 0.5 * (sx + tx);
                                    ty = 0.5 * (sy + ty);
                                }
                                sx = tx;
                                sy = ty;
                            }
                        }
                        float nx = (sx - qx) / camera.z;
                        float ny = (sy - qy) / camera.w;
                        if (nx * nx + ny * ny <= (R + rr + 1.0) * (R + rr + 1.0)
                                && DOM(sx, sy)) {
                            // A projection is only useful if it converged: the residual at
                            // the new point must be below a quarter stroke radius.
                            float rf = F(sx, sy);
                            float rgx = FX(sx, sy) * camera.z;
                            float rgy = FY(sx, sy) * camera.w;
                            float rg = max(sqrt(rgx * rgx + rgy * rgy), 1e-9);
                            bool okr = (rf == rf) && (abs(rf) < 1e30)
                                    && (abs(rf) <= 0.25 * R * rg);
                            if (okr) {
                                float dxp = (x - sx) / camera.z;
                                float dyp = (y - sy) / camera.w;
                                d = min(d, sqrt(dxp * dxp + dyp * dyp));
                            }
                        }
                    }
                }
            }
        }
    }

    // ---- GeoGebra-style marching-square cell (GPU) ----
    // A pixel is one marching cell. GGB's AdaptiveQuadTree / Bernstein plotters never trust a
    // single point sample: they use the four corner signs and interpolate the crossings. The
    // first-order distance above is still the sub-pixel stroke; this cell test adds the
    // coverage a cell whose centre projection fails (a thin or tangent branch) would lose.
    // It is evaluated only when all four corners are in the domain and no pole factor changes
    // sign between them — a pole crossing a cell is a jump, not a contour segment.
    float march_cov = 0.0;
    if (dom) {
        float x0 = x - 0.5 * camera.z;
        float x1 = x + 0.5 * camera.z;
        float y0 = y - 0.5 * camera.w;
        float y1 = y + 0.5 * camera.w;
        bool okc = DOM(x0, y0) && DOM(x1, y0) && DOM(x0, y1) && DOM(x1, y1);
        float f00 = 0.0;
        float f10 = 0.0;
        float f11 = 0.0;
        float f01 = 0.0;
        if (okc) {
            f00 = F(x0, y0);
            f10 = F(x1, y0);
            f11 = F(x1, y1);
            f01 = F(x0, y1);
            okc = (f00 == f00) && (abs(f00) < 1e30)
               && (f10 == f10) && (abs(f10) < 1e30)
               && (f11 == f11) && (abs(f11) < 1e30)
               && (f01 == f01) && (abs(f01) < 1e30);
        }
        if (okc) {
            float p00 = POLE(x0, y0);
            float p10 = POLE(x1, y0);
            float p11 = POLE(x1, y1);
            float p01 = POLE(x0, y1);
            okc = p00 * p10 > 0.0 && p10 * p11 > 0.0
               && p11 * p01 > 0.0 && p01 * p00 > 0.0;
        }
        if (okc) {
            // corner order: bottom-left, bottom-right, top-right, top-left. Screen positions
            // use the same order (screen y grows down, so the bottom corners are +0.5).
            float cf[4] = float[4](f00, f10, f11, f01);
            vec2 cp[4] = vec2[4](vec2(-0.5, 0.5), vec2(0.5, 0.5),
                                 vec2(0.5, -0.5), vec2(-0.5, -0.5));
            vec2 pts[4];
            int pedge[4];
            int npts = 0;
            for (int e = 0; e < 4; e++) {
                int b = (e + 1) & 3;
                if ((cf[e] >= 0.0) != (cf[b] >= 0.0)) {
                    float tt = cf[e] / (cf[e] - cf[b]);
                    pts[npts] = mix(cp[e], cp[b], clamp(tt, 0.0, 1.0));
                    pedge[npts] = e;
                    npts++;
                }
            }
            if (npts == 2) {
                march_cov = clamp(R - _seg_dist(vec2(0.0), pts[0], pts[1]), 0.0, 1.0);
            } else if (npts == 4) {
                // The saddle case: use the cell centre to choose the asymptotic pairing.
                // If corner 0 has the centre's sign, the isolated corners are 1 and 3
                // (pair 0-1 and 2-3); otherwise they are 0 and 2 (pair 3-0 and 1-2).
                bool same = (cf[0] >= 0.0) == (f >= 0.0);
                int pa = same ? 0 : 3;
                int pb = same ? 1 : 0;
                int pc = same ? 2 : 1;
                int pd = same ? 3 : 2;
                vec2 a0 = vec2(0.0);
                vec2 a1 = vec2(0.0);
                vec2 b0 = vec2(0.0);
                vec2 b1 = vec2(0.0);
                for (int i = 0; i < 4; i++) {
                    if (pedge[i] == pa) a0 = pts[i];
                    if (pedge[i] == pb) a1 = pts[i];
                    if (pedge[i] == pc) b0 = pts[i];
                    if (pedge[i] == pd) b1 = pts[i];
                }
                march_cov = max(clamp(R - _seg_dist(vec2(0.0), a0, a1), 0.0, 1.0),
                                clamp(R - _seg_dist(vec2(0.0), b0, b1), 0.0, 1.0));
            }
        }
    }

    // A singular cell (|grad F| -> 0, GGB's ImplicitCurveMarchingRect.singular) has no
    // sign change and no useful Newton direction. Draw its tiny disc so an isolated point
    // such as x² + y² = 0 is not left to the projection alone.
    // GGB measures the singular cell in *world* derivative units (|Fx| + |Fy| < 0.001);
    // the per-pixel gradient of an ordinary circle is ~0.01 and must not qualify.
    // The distance gate is essential for a scaled expression: (x+y)/(x*y) has |F| < 0.05 and
    // |Fx|+|Fy| < 0.001 over huge regions around ±50, although the level set is far away. A
    // singular point must also be *on* the set to the first order: |F| ~ R*|grad F|. The
    // ratio is scale-invariant, unlike the two absolute thresholds above.
    float gw_s = abs(FX(x, y)) + abs(FY(x, y));
    float gnorm_s = max(sqrt(gx * gx + gy * gy), 1e-9);
    if (dom && (f == f) && (abs(f) < 1e30) && gw_s < 1e-3 && abs(f) < 0.05
            && abs(f) < R * gnorm_s) {
        march_cov = 1.0;
    }

    float cov = 0.0;
    if (dom) {
        float gnorm = max(sqrt(gx * gx + gy * gy), 1e-9);
        float nx = gx / gnorm;
        float ny = gy / gnorm;
        float stepx = nx * R * camera.z;
        float stepy = ny * R * camera.w;
        // Probe coordinates are hoisted into locals first: F's macro body is printed bare
        // (`x*x`, not `(u)*(u)`), so a shifted argument would expand textually as
        // `x + stepx*x + stepx` — AGENTS item 1, re-armed.
        float pxp = x + stepx;
        float pyp = y + stepy;
        float pxm = x - stepx;
        float pym = y - stepy;

        // Pole guard: a simple pole (tan) is monotone across the jump, so the first-order
        // distance can be a pseudo-zero and paint the asymptote (measured: a horizontal
        // connector at y = pi/2 + k*pi when the y view is wide). A probe that crosses a
        // zero of the pole product is not a continuous chord.
        float pole = POLE(x, y);
        bool crosses = pole * POLE(pxp, pyp) <= 0.0
                    || pole * POLE(pxm, pym) <= 0.0;

        // Certify the first-order distance by *projecting onto the level set* (Newton):
        // |F|/|grad F| is a pseudo-zero at a pole and at a diverging domain boundary (log),
        // where the value is large but the gradient is far larger. A converged projection
        // leaves a residual below a quarter stroke radius, and its distance to the pixel is
        // the actual distance. This is the 1-D march's tangent-foot seed, one dimension up.
        bool ok = false;
        float dproj = 1e30;
        bool finite_here = (f == f) && (abs(f) < 1e30);
        if (finite_here && !crosses && abs(f) / gnorm < R + 1.0) {
            float sx = x;
            float sy = y;
            for (int it = 0; it < 2; it++) {
                float ff = F(sx, sy);
                float fx = FX(sx, sy);
                float fy = FY(sx, sy);
                float den = fx * fx + fy * fy;
                if (den > 1e-18) {
                    float tx = sx - ff * fx / den;
                    float ty = sy - ff * fy / den;
                    for (int h = 0; h < 4 && !DOM(tx, ty); h++) {
                        tx = 0.5 * (sx + tx);
                        ty = 0.5 * (sy + ty);
                    }
                    sx = tx;
                    sy = ty;
                }
            }
            float rf = F(sx, sy);
            float rfx = FX(sx, sy) * camera.z;
            float rfy = FY(sx, sy) * camera.w;
            float rg = max(sqrt(rfx * rfx + rfy * rfy), 1e-9);
            bool conv = (rf == rf) && (abs(rf) < 1e30) && DOM(sx, sy)
                    && abs(rf) <= 0.25 * R * rg;
            if (conv) {
                float dxp = (x - sx) / camera.z;
                float dyp = (y - sy) / camera.w;
                dproj = sqrt(dxp * dxp + dyp * dyp);
                ok = dproj <= R + 0.5;
            }
        }
        cov = max(ok ? clamp(R - dproj, 0.0, 1.0) : 0.0, march_cov);
    } else {
        cov = max(clamp(R - d, 0.0, 1.0), march_cov);
    }

    float a = cov * color.a * qt_Opacity;
    fragColor = vec4(color.rgb * a, a);   // premultiplied alpha
}
"""


def implicit_shader_sources(expr: sp.Expr) -> tuple[str, str]:
    """Vertex/fragment shader sources for an implicit curve (the level set ``F(x, y) = 0``).

    Same uniform block as the 1-D shader, so the QML side and the curve model need no special
    case: only the expressions differ. The domain predicate comes from the same sympy analysis
    the 1-D path uses, printed over both symbols (F lives on two of them).
    """
    conds = _domain_conditions(expr)
    dom = " && ".join(f"({func_glsl(c)})" for c in conds) if conds else "true"
    return (VERTEX_SHADER, IMPLICIT_FRAGMENT_TEMPLATE
            .replace("@FUNC@", func_glsl(expr, "x"))
            .replace("@DFUNCX@", func_glsl(sp.diff(expr, sp.Symbol("x")), "x"))
            .replace("@DFUNCY@", func_glsl(sp.diff(expr, sp.Symbol("y")), "x"))
            .replace("@DOMXY@", dom)
            .replace("@POLEXY@", _pole_glsl(expr)))

def shader_sources(expr: sp.Expr) -> tuple[str, str]:
    """Vertex/fragment shader sources for the QML front end (implicit model)."""
    # f'(x) is the march's slope bootstrap, an optimization rather than a requirement:
    # an unprintable derivative (Derivative(floor(x)) and friends) must not kill the
    # curve. A NaN bootstrap sends the march down its probing path instead.
    try:
        dfunc = dfunc_glsl(expr)
    except Exception:  # noqa: BLE001 — the printer raises its own error types
        dfunc = "0.0 / 0.0"
    try:
        interval_fn = interval_glsl(expr)
        use_interval = 1
    except Unsupported:
        interval_fn = ""
        use_interval = 0
    frag = (
        FRAGMENT_TEMPLATE.replace("@FUNC@", func_glsl(expr, "u", wrap=True))
        .replace("@DFUNC@", dfunc)
        .replace("@USE_INTERVAL@", str(use_interval))
        .replace("@INTERVAL_LIB@", INTERVAL_LIB if use_interval else "")
        .replace("@INTERVAL_FN@", interval_fn)
        .replace(
            "@HAS_SIGN_POLE@",
            "true" if (use_interval and has_sign_pole(expr)) else "false",
        )
        .replace(
            "@HAS_DEPENDENT_QUOTIENT@",
            "true" if has_dependent_quotient(expr) else "false",
        )
        .replace(
            "@POLE@",
            _pole_glsl(expr, "u") if has_sign_pole(expr) else "1.0",
        )
    )
    if use_interval:
        edge_hit = edge_du = ""
    else:
        edge_hit, edge_du = _edge_glsl(expr)
    frag = frag.replace("@EDGEHIT@", edge_hit).replace("@EDGEDU@", edge_du)
    # Domain: the macro argument is parenthesized, same reasoning as F (macros are
    # text substitution)
    dom = domain_glsl(expr, "u")
    frag = frag.replace(
        "@DOM@",
        f"#define DOM(u) ({dom})" if dom else "#define DOM(u) true",
    )
    frag = frag.replace(
        "@DOMS@",
        "#define DOMS(u) true" if _domain_is_pole_only(expr) else "#define DOMS(u) DOM(u)",
    )
    return (VERTEX_SHADER, frag)
