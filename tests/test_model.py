"""Model-layer unit tests: expression -> GLSL, shader sources, view-rectangle math (no Qt)."""

import pytest
import sympy as sp

from qmlmathplot import model

X = sp.Symbol("x")


@pytest.mark.parametrize(("expr", "expected"), [
    (X**2, "(x*x)"),
    (X**-2, "(1.0/(x*x))"),
    (X**9, "pow(x, 9.0)"),
    (sp.sin(X) / X, "SIN(x)/x"),
    (sp.exp(-X * X) * sp.sin(10 * X), "exp(-(x*x))*SIN(10*x)"),
])
def test_func_glsl(expr, expected):
    """Small integer powers must be expanded into repeated multiplication (GLSL's
    pow(x, y) is undefined for x<0 and drivers often return NaN); sin/cos must be
    wrapped as SIN/COS (argument reduction, see the note in model)."""
    assert model.func_glsl(expr) == expected


def test_func_glsl_renames_variable():
    assert model.func_glsl(sp.sin(1 / X), "u") == "SIN(1.0/u)"


def test_dfunc_glsl():
    assert model.dfunc_glsl(sp.sin(1 / X)) == "-COS(1.0/x)/(x*x)"


def test_func_glsl_wraps_macro_parameter():
    """wrap=True prints the macro argument as (u): a macro is textual substitution,
    and without that layer of parentheses the meaning changes."""
    assert model.func_glsl(sp.sin(1 / X), "u", wrap=True) == "SIN(1.0/(u))"


def test_shader_sources_inject_expression():
    vert, frag = model.shader_sources(sp.sin(1 / X))
    assert vert == model.VERTEX_SHADER
    assert "@FUNC@" not in frag
    assert "@DFUNC@" not in frag
    # Macros are textual substitution: the argument needs parentheses, otherwise
    # F(x - h) would expand to 1.0/x - h
    assert "#define F(u) (SIN(1.0/(u)))" in frag
    assert "#define DF -COS(1.0/x)/(x*x)" in frag  # the derivative is still evaluated in x


def test_zoom_round_trip_is_exact():
    rect = model.ViewRect()
    before = rect.as_tuple()
    rect.zoom(120, 0.3, 0.4)
    assert rect.as_tuple() != before
    rect.zoom(-120, 0.3, 0.4)
    assert rect.as_tuple() == pytest.approx(before)


def test_zoom_keeps_anchor_world_point():
    rect = model.ViewRect()
    anchor = rect.world_at(0.25, 0.75)
    rect.zoom(120, 0.25, 0.75)
    assert rect.world_at(0.25, 0.75) == pytest.approx(anchor)


def test_pan_pixels_moves_by_view_fraction():
    rect = model.ViewRect()
    rect.pan_pixels(90, 60, 900, 600)  # 1/10 of the width and height
    assert rect.as_tuple() == pytest.approx((-7.2, 4.8, -1.6, 2.4))


def test_pan_ignores_zero_size():
    rect = model.ViewRect()
    before = rect.as_tuple()
    rect.pan_pixels(10, 10, 0, 0)
    assert rect.as_tuple() == before


def test_reset_restores_default_view():
    rect = model.ViewRect()
    rect.zoom(240, 0.5, 0.5)
    rect.reset()
    assert rect.as_tuple() == model.DEFAULT_VIEW


def test_pole_and_domain_analysis():
    """Pole factors (a sign change = a +inf/-inf jump) and domain conditions."""
    assert model.pole_glsl(1 / X) == "(x)"
    assert model.domain_glsl(1 / X) == "(x != 0)"
    assert model.pole_glsl(sp.tan(X)) == "(COS(x))"      # tan's poles are where cos(x)=0
    assert model.domain_glsl(sp.tan(X)) == "(COS(x) != 0)"
    assert model.domain_glsl(sp.log(X)) == "(x > 0)"
    assert model.domain_glsl(sp.sqrt(X)) == "(x >= 0)"
    assert model.pole_glsl(X**2) is None                 # continuous everywhere, no pole factor
    assert model.domain_glsl(X**2) is None               # the whole domain


def test_shader_sources_inject_domain_and_pole():
    _, frag = model.shader_sources(1 / X)
    assert "#define DOM(u) ((u != 0))" in frag
    assert "#define POLE(u) ((u))" in frag
    _, smooth = model.shader_sources(X**2)
    assert "#define DOM(u) true" in smooth               # not analysable => identically true
    assert "#define POLE(u) 1.0" in smooth               # no pole => the product stays positive, no false positives




def test_unbounded_edges():
    """Which domain boundaries need "infinite extension": only the slowly diverging ones.

    log(x) tends to -inf as x->0+, but the sampling window has a fixed width and cannot
    reach that deep, so in a deep viewport the whole curve would vanish and has to be
    added back by the analytically derived boundary ray. sqrt(x) is bounded at 0 (0), and
    sin(1/x) has no limit at 0, so neither needs it; 1/x diverges fast and sampling is
    naturally deep enough, but marking both directions analytically does no harm.
    """
    assert model.unbounded_edges(sp.log(X)) == [(0.0, True, False)]
    assert model.unbounded_edges(sp.sqrt(X)) == []
    assert model.unbounded_edges(sp.sin(1 / X)) == []
    assert model.unbounded_edges(X**2) == []
    assert sorted(model.unbounded_edges(1 / X)) == [(0.0, True, True)]
    assert model.unbounded_edges(1 / X**2) == [(0.0, False, True)]


def test_effective_view_keeps_the_requested_scale_ratio() -> None:
    """`aspect` expands the view around its center — never crops — until the y-unit and the
    x-unit are drawn in the given ratio (1.0 = square units)."""
    rect = model.ViewRect(-6.0, 6.0, -2.0, 2.0)

    # "follow the view": the rect is used as-is (the shape follows the widget)
    assert rect.effective(900, 600, None) == (-6.0, 6.0, -2.0, 2.0)

    # square units on a 3:2 widget: y expands, x is untouched
    xmin, xmax, ymin, ymax = rect.effective(900, 600, 1.0)
    assert (xmin, xmax) == (-6.0, 6.0)
    assert (ymin, ymax) == (-4.0, 4.0)
    assert (600 / (ymax - ymin)) == pytest.approx(900 / (xmax - xmin))

    # the default view already has a 2:1 pixel ratio on this widget -> unchanged
    assert rect.effective(900, 600, 2.0) == (-6.0, 6.0, -2.0, 2.0)

    # the invariant holds for any widget shape and any ratio, and nothing visible is cropped
    for width, height in ((900, 600), (600, 900), (1600, 400), (400, 1600), (500, 500)):
        for aspect in (0.5, 1.0, 2.0):
            xmin, xmax, ymin, ymax = rect.effective(width, height, aspect)
            assert xmin <= -6.0 and xmax >= 6.0 and ymin <= -2.0 and ymax >= 2.0
            assert (height / (ymax - ymin)) == pytest.approx(aspect * width / (xmax - xmin))
