"""Model layer: expression -> GLSL, shader source templates, view-rect (pan/zoom) math.

This layer imports no Qt and can be tested standalone without a GUI; both the ViewModel
(PlotController) and the View (the QML ShaderEffect) build on it.

The drawing model is **implicit (signed-distance)**: the fragment shader computes, per
pixel, the first-order screen-space distance "to the curve". Cost per frame is
proportional to the pixel count and independent of function frequency (oscillatory
functions cannot drag the frame rate down through aliasing); a column that packs several
oscillations into one pixel is filled as a [min,max] envelope band instead — see the
comment above FRAGMENT_TEMPLATE.
"""

from __future__ import annotations

import re

import sympy as sp
from sympy.printing.glsl import GLSLPrinter
from sympy.printing.precedence import PRECEDENCE

__all__ = [
    "DEFAULT_VIEW",
    "FRAGMENT_TEMPLATE",
    "VERTEX_SHADER",
    "ViewRect",
    "dfunc_glsl",
    "func_glsl",
    "shader_sources",
]

# Default view (world coordinates)
DEFAULT_VIEW = (-6.0, 6.0, -2.0, 2.0)


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


def _domain_conditions(expr: sp.Expr) -> list[sp.Expr]:
    """Collect domain conditions (sympy expressions; each must hold identically to be
    inside the domain).

    Covers the common domain boundaries: denominator != 0, log argument > 0, sqrt argument
    >= 0, asin/acos argument ∈ [-1,1], cos != 0 for tan/sec, sin != 0 for cot/csc.
    """
    out: list[sp.Expr] = []
    if isinstance(expr, sp.Pow) and expr.exp.is_number and expr.exp.is_negative:
        out.append(sp.Ne(expr.base, 0))
    elif isinstance(expr, sp.Pow) and expr.exp == sp.Rational(1, 2):
        out.append(sp.Ge(expr.base, 0))
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


def pole_glsl(expr: sp.Expr, name: str = "x") -> str | None:
    """Product of the pole denominators (GLSL). Evaluated at both interval ends; a product
    <= 0 means the interval crosses a pole."""
    factors = _pole_factors(expr)
    if not factors:
        return None
    return " * ".join(f"({func_glsl(f, name)})" for f in factors)


def domain_glsl(expr: sp.Expr, name: str = "x") -> str | None:
    """Domain predicate (a GLSL relational expression). Returns None when it cannot be
    analysed."""
    conds = _domain_conditions(expr)
    if not conds:
        return None
    return " && ".join(f"({func_glsl(c, name)})" for c in conds)


def dfunc_glsl(expr: sp.Expr, name: str = "x") -> str:
    """GLSL text for f'(x) (used by the stroke criterion and the pole criterion)."""
    return func_glsl(sp.diff(expr, sp.Symbol("x")), name)


class ViewRect:
    """View rectangle (world coordinates) plus pan/zoom operations; owned by the ViewModel."""

    ZOOM_PER_STEP = 0.9  # one step up on the wheel: span ×0.9 (10% zoom in)
    MIN_SPAN = 1e-9  # span limits, to avoid zooming to 0 (can never scroll back) or to inf
    MAX_SPAN = 1e9

    __slots__ = ("xmin", "xmax", "ymin", "ymax")

    def __init__(self, xmin: float = -6.0, xmax: float = 6.0,
                 ymin: float = -2.0, ymax: float = 2.0) -> None:
        self.xmin, self.xmax, self.ymin, self.ymax = xmin, xmax, ymin, ymax

    # ---- read-only view ----
    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.xmin, self.xmax, self.ymin, self.ymax)

    def reset(self) -> None:
        self.xmin, self.xmax, self.ymin, self.ymax = DEFAULT_VIEW

    def effective(self, width: float, height: float, aspect: float | None
                  ) -> tuple[float, float, float, float]:
        """The rect actually drawn in a `width` x `height` viewport.

        ``aspect is None`` means "follow the view": the rect is used as-is, so the two axes
        scale independently and the shape follows the widget's aspect ratio. With a number
        (matplotlib's convention: pixels per y-unit over pixels per x-unit, 1.0 = square
        units), the rect is **expanded around its center** — never cropped — until the two
        scales have that ratio. So the plot keeps filling the widget and nothing that was
        visible disappears; one axis simply shows more world.
        """
        if aspect is None or aspect <= 0 or width <= 0 or height <= 0:
            return self.as_tuple()
        span_x = self.xmax - self.xmin
        span_y = self.ymax - self.ymin
        if span_x <= 0 or span_y <= 0:
            return self.as_tuple()
        # current (pixels per y-unit) / (pixels per x-unit)
        pixel_ratio = (height / span_y) / (width / span_x)
        if pixel_ratio <= 0:
            return self.as_tuple()
        cx = 0.5 * (self.xmin + self.xmax)
        cy = 0.5 * (self.ymin + self.ymax)
        if pixel_ratio > aspect:            # y drawn too tall -> show more y (ratio down)
            span_y *= pixel_ratio / aspect
        else:                               # x drawn too wide -> show more x (ratio up)
            span_x *= aspect / pixel_ratio
        return (cx - 0.5 * span_x, cx + 0.5 * span_x, cy - 0.5 * span_y, cy + 0.5 * span_y)

    def world_at(self, u: float, v: float) -> tuple[float, float]:
        """World coordinates for a normalized screen position (0~1, top-left origin)."""
        return (self.xmin + (self.xmax - self.xmin) * u,
                self.ymax - (self.ymax - self.ymin) * v)

    # ---- pan ----
    def pan_pixels(self, dx: float, dy: float, width: float, height: float) -> None:
        """Pan by a pixel displacement (screen y points down, world y points up)."""
        if width <= 0 or height <= 0:
            return
        world_dx = (self.xmax - self.xmin) * dx / width
        world_dy = (self.ymax - self.ymin) * dy / height
        self.xmin -= world_dx
        self.xmax -= world_dx
        self.ymin += world_dy
        self.ymax += world_dy

    # ---- zoom ----
    def zoom(self, delta: float, u: float, v: float) -> None:
        """Zoom anchored at the normalized position (u, v); delta is the wheel increment
        (120 = one step).

        The scale factor depends only on delta, so scrolling up and down at the same
        position are exact inverses; the anchor scales by "distance to the anchor", so the
        world point under the cursor is not panned away.
        """
        if delta == 0:
            return
        scale = self.ZOOM_PER_STEP ** (delta / 120.0)

        span_x = self.xmax - self.xmin
        span_y = self.ymax - self.ymin
        anchor_x, anchor_y = self.world_at(u, v)

        span_x = min(max(span_x * scale, self.MIN_SPAN), self.MAX_SPAN)
        span_y = min(max(span_y * scale, self.MIN_SPAN), self.MAX_SPAN)

        self.xmin = anchor_x - span_x * u
        self.xmax = anchor_x + span_x * (1.0 - u)
        self.ymin = anchor_y - span_y * (1.0 - v)
        self.ymax = anchor_y + span_y * v


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

# Implicit (signed-distance) drawing — a puzzle in two pieces:
#
#   1) Stroke: per pixel, compute its screen-space distance to the polyline (segments
#      joining adjacent samples), yielding a constant-width, anti-aliased line; steep
#      segments neither thicken nor break. Uses "distance to the segment" rather than
#      "distance to the tangent", which overestimates at strong curvature and makes the
#      line thin out or vanish (Desmos' approach).
#   2) Envelope band: when one pixel's x interval packs several oscillations (local period
#      < 1 pixel), the per-pixel distance is meaningless (it draws as moiré/random
#      aliasing). Fill [min,max] as a solid band instead — for sin(1/x) this is exactly
#      its true ±1 envelope.
#
# When does a column count as "packing several oscillations" (all within the ±1-column
# narrow window; ordinary pixels sample 8 points, banded columns add up to 16):
#   a) >= 2 direction turns within the column: the curve reverses up/down several times
#      => several oscillations (a single turn is a resolvable extremum and is stroked
#      normally);
#   d) the derivative prediction |f'|·dx far exceeds the measured spread (hard threshold
#      16×, with pred >= 4px): when the oscillation period is far below one column,
#      sampling can miss it (8 points may happen to look monotone), and this criterion is
#      independent of sampling luck, so it catches that case.
# The band's top/bottom edges use a wider ±8-column, 16-point window to estimate the
# envelope, so the edges do not bite out dark seams/steps. A pole (denominator changes
# sign and values far exceed the viewport) gets its own gap; no vertical +∞/-∞ connector
# is drawn.
#
# The expression is a macro rather than a user function: one pixel evaluates the same x
# 1 + 8 + 16 = 25 times (stroke samples + narrow window + wide window), and a macro is
# preprocessor expansion with no function-call semantics — exactly what is needed.
FRAGMENT_TEMPLATE = """#version 440
layout(location = 0) in vec2 vUV;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 view;        // xmin, xmax, ymin, ymax
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

// Point-to-segment distance (screen space). Stroking by segment => constant width
// throughout.
float _seg_dist(vec2 p, vec2 a, vec2 b) {
    vec2 ab = b - a;
    float t = clamp(dot(p - a, ab) / max(dot(ab, ab), 1e-12), 0.0, 1.0);
    return length(p - (a + t * ab));
}

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

#define F(u) (@FUNC@)
#define DF @DFUNC@
// Domain predicate (pixels outside the domain are not drawn) and "pole denominators"
// (sign change => the interval crosses a pole). DOMS is the per-sample variant: for a
// purely pole-shaped domain (tan, 1/x, sec, …) it is `true`, because there the NaN/inf
// test on the value already rejects out-of-domain samples.
@DOM@
@DOMS@
@POLE@

void main() {
    float x = mix(view.x, view.y, vUV.x);
    float y = mix(view.w, view.z, vUV.y);

    // Domain: pixels outside are not drawn (x<0 for log(x), x<0 for sqrt(x), |x|>1 for
    // asin, …)
    if (!DOM(x)) {
        fragColor = vec4(0.0);
        return;
    }

    float spanx = view.y - view.x;
    float spany = view.w - view.z;
    float dx = spanx / size.x;      // world x per pixel
    float sx = size.x / spanx;      // pixels per world x
    float sy = size.y / spany;      // pixels per world y

    float f0 = F(x);
    float d1 = DF;

    // ---- 1) sampling + polyline stroke ----
    // Desmos' approach (engineering.desmos.com): join the sample points into **segments**
    // and stroke by segment. The width stays constant everywhere, whereas "distance to the
    // tangent" overestimates on steep/strongly curved parts and makes the line thin out and
    // eventually vanish (log(x) as x→0+, steep parts of sin(1/x)).
    // Sampling half-step: 8 samples span ±1 column. The stroke needs a window that wide —
    // a pixel's perpendicular foot on a sloped line is displaced along x by m·d/√(1+m²)
    // (up to ~1 px), so with a narrower polyline the foot falls outside the segments and the
    // distance degrades to "distance to the nearest segment endpoint": the coverage collapses
    // and the stroke looks thin and ragged on steep parts (measured before the fix: 58 % of
    // the nominal width at 84°, 85 % at 63°). A wider window (±2 columns) fixes the width
    // fully but over-triggers the band criteria, which are calibrated for this one.
    float h = dx;
    float nl = 1e30;
    float nh = -1e30;
    // 8 points: the **even slots** of a 16-point grid (±1 column, 0.125-column spacing).
    // Ordinary pixels evaluate only these 8; banded columns add the odd slots to make 16
    // and count turns there (see below).
    float smp[8];
    float smp_x[8];
    bool smp_ok[8];
    for (int i = 0; i < 8; i++) {
        float t = -1.0 + (float(i) + 0.25) / 4.0;           // even slot of the 16-point grid
        float xi = x + t * h;
        float s = F(xi);
        // Skip out-of-domain/NaN/inf (pole) samples: they join neither the envelope nor the
        // segments. DOM is included because GLSL's log(negative) etc. are undefined and
        // some drivers return finite garbage.
        bool ok = DOMS(xi) && (s == s) && (abs(s) < 1e30);
        smp[i] = s;
        smp_x[i] = xi;
        smp_ok[i] = ok;
        if (ok) {
            nl = min(nl, s);
            nh = max(nh, s);
        }
    }

    // ---- 2) band decision (cheap: reuses the samples above) ----
    float w = 0.0;          // band weight: 0 = stroke only, 1 = solid envelope band
    bool gap = false;       // this column straddles a pole -> draw nothing at all
    if (abs(d1) * dx * sy > 1.0) {
        float spread = (nh - nl) * sy;                  // measured spread (px, narrow window)
        float hp = 0.5 * dx;
        // A true jump: this column crosses a pole (denominator sign change) and the values
        // far exceed the viewport => the +∞/-∞ connector is not drawn (removes the vertical
        // connector of 1/x, tan(x) at asymptotes). A **bounded** oscillation such as
        // sin(1/x) never exceeds the viewport and is still represented by the envelope band.
        // "Sample spread > 32× the viewport height" rather than a pixel threshold: pixels
        // drift with zoom, world units do not.
        if (POLE(x - hp) * POLE(x + hp) <= 0.0 && (nh - nl) > 32.0 * spany) {
            gap = true;
        } else {
            // Turn counting at 16 points: fill in the odd slots of the 16-point grid,
            // interleaved with the narrow window's even slots. Computed only here — this is
            // where accuracy matters, and other pixels save these 8 F evaluations.
            float turns16 = 0.0;
            {
                bool have = false;
                float ps = 0.0;
                float pd = 0.0;
                for (int k = 0; k < 16; k++) {
                    float s;
                    bool ok;
                    if (k - (k / 2) * 2 == 0) {         // even slot: reuse a narrow-window sample
                        s = smp[k / 2];
                        ok = smp_ok[k / 2];
                    } else {                             // odd slot: sample additionally
                        float xi = x + (-1.0 + (float(k) + 0.5) / 8.0) * h;
                        s = F(xi);
                        ok = DOMS(xi) && (s == s) && (abs(s) < 1e30);
                    }
                    if (ok) {
                        if (have) {
                            float d = s - ps;
                            if (pd * d < 0.0) { turns16 += 1.0; }
                            pd = d;
                        }
                        ps = s;
                        have = true;
                    }
                }
            }
            float pred = max(abs(d1) * dx * sy, 2.0);   // derivative-predicted spread (pixels)
            w = step(2.0, turns16);                     // a) >= 2 turns in the column (16 points)
            // d) The threshold 16 is measured: a monotone concave curve such as log gives a
            //    ratio ~3.6, branches of tan/1/x <1, and a truly sub-pixel sin(1/x) ~58;
            //    pred >= 4px excludes "resolvable extrema" (at a peak pred≈0, spread≈0, and
            //    the ratio would misjudge).
            w = max(w, step(16.0 * spread + 1e-6, pred) * step(4.0, pred));
            w *= smoothstep(1.5, 4.0, spread);          // no point filling a band < 1.5 pixels
        }
    }

    // ---- 3) stroke + band, each paid for only when it survives ----
    // A fully banded column (w >= 1) discards the stroke anyway (mixing with w = 1 keeps
    // only the band), a stroke-only column (w == 0) never needs the wide window, and a pole
    // gap draws nothing. So the per-pixel cost is 1 + 8 F evaluations in the common case,
    // 1 + 8 + 8 when the turn count is needed, and 1 + 8 + 8 + 16 for a solid band.
    float cov = 0.0;
    if (!gap) {
        if (w < 1.0) {
            // Stroke: distance to the polyline of sampled segments (Desmos' approach,
            // engineering.desmos.com). Constant width everywhere, whereas "distance to the
            // tangent" overestimates on steep/strongly curved parts and makes the line thin
            // out and eventually vanish (log(x) as x→0+, steep parts of sin(1/x)).
            vec2 p = vec2(x * sx, y * sy);      // screen space (pan does not affect distance)
            float dmin = 1e30;
            float prev_xi = 0.0;
            float prev_si = 0.0;
            bool prev_oki = false;
            for (int i = 0; i < 8; i++) {
                if (smp_ok[i]) {
                    if (prev_oki) {
                        dmin = min(dmin, _seg_dist(p, vec2(prev_xi * sx, prev_si * sy),
                                                      vec2(smp_x[i] * sx, smp[i] * sy)));
                    }
                    prev_xi = smp_x[i];
                    prev_si = smp[i];
                }
                prev_oki = smp_ok[i];
            }
            // Infinite extension at unbounded boundaries: when the window crosses a domain
            // boundary where the function tends to ±∞, pull a vertical ray from the nearest
            // valid sample along the boundary to ±1e12, so a slowly diverging branch
            // (log(x) as x→0+) is drawn to any depth. The ray lies on the boundary x, so the
            // width stays constant and the columns align.
            float ex_ = _edge_hit(x - h, x + h);
            if (ex_ < 1e29 && nl < 1e29) {
                vec2 du_ = _edge_du(ex_);
                if (du_.x != 0.0) {
                    dmin = min(dmin, _seg_dist(p, vec2(ex_ * sx, nl * sy), vec2(ex_ * sx, -1e12)));
                }
                if (du_.y != 0.0) {
                    dmin = min(dmin, _seg_dist(p, vec2(ex_ * sx, nh * sy), vec2(ex_ * sx, 1e12)));
                }
            }
            cov = clamp(lineWidth * 0.5 + 0.5 - dmin, 0.0, 1.0);
        }
        if (w > 0.0) {
            // The wide window (±8 columns, 16 points) only estimates the band's top/bottom
            // edges: a wider window is more likely to hit the extrema, so the edges do not
            // develop dark seams/steps from a too-narrow envelope.
            float wl = 1e30;
            float wh = -1e30;
            for (int i = 0; i < 16; i++) {
                float t = -8.0 + 16.0 * (float(i) + 0.5) / 16.0;    // ±8 columns
                float s = F(x + t * h);
                if (s == s && abs(s) < 1e30) {
                    wl = min(wl, s);
                    wh = max(wh, s);
                }
            }
            float band = clamp((wh - y) * sy + 2.0, 0.0, 1.0)
                       * clamp((y - wl) * sy + 2.0, 0.0, 1.0);
            // A banded column is replaced wholesale by the envelope band: the polyline is
            // random aliasing there, while the band is the true value range. w = 0 columns
            // keep the polyline stroke (constant width).
            cov = mix(cov, band, w);
        }
    }

    float a = cov * color.a * qt_Opacity;
    if (isnan(f0) || isinf(f0)) {
        a = 0.0;                       // discard points like sin(1/0), 1/0 outright
    }
    fragColor = vec4(color.rgb * a, a);   // premultiplied alpha
}
"""


def shader_sources(expr: sp.Expr) -> tuple[str, str]:
    """Vertex/fragment shader sources for the QML front end (implicit model)."""
    frag = (
        FRAGMENT_TEMPLATE.replace("@FUNC@", func_glsl(expr, "u", wrap=True))
        .replace("@DFUNC@", dfunc_glsl(expr))
    )
    edge_hit, edge_du = _edge_glsl(expr)
    frag = frag.replace("@EDGEHIT@", edge_hit).replace("@EDGEDU@", edge_du)
    # Domain / pole: the macro argument is parenthesized, same reasoning as F (macros are
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
    pole = pole_glsl(expr, "u")
    frag = frag.replace(
        "@POLE@",
        f"#define POLE(u) ({pole})" if pole else "#define POLE(u) 1.0",
    )
    return (VERTEX_SHADER, frag)
