"""Model-layer unit tests: expression -> GLSL, shader sources, camera math, nice ticks.

No Qt widgets and no GPU: the camera is a plain QObject and the tick algorithm is pure, so
the whole view arithmetic is testable without a scene graph.
"""

import pytest
import sympy as sp

from qmlmathplot import Camera, model, nice_ticks

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
    assert "@DOM@" not in frag and "@POLE@" not in frag and "@EDGEHIT@" not in frag


def test_shader_sources_use_the_camera():
    """The fragment shader maps through the camera (centre + units per logical pixel), not
    through a view rectangle: the drawing must not depend on a size round-trip."""
    _, frag = model.shader_sources(sp.sin(X))
    assert "vec4 camera;" in frag
    assert "vec4 view;" not in frag
    assert "float x = camera.x + (vUV.x - 0.5) * size.x * camera.z;" in frag
    assert "float y = camera.y + (0.5 - vUV.y) * size.y * camera.w;" in frag
    # the sampling code reads the scales off the camera, not off a span
    assert "float dx = camera.z;" in frag
    assert "float sx = 1.0 / camera.z;" in frag
    assert "float sy = 1.0 / camera.w;" in frag


def test_pole_and_domain_analysis():
    """Pole factors (a sign change = a +inf/-inf jump) and domain conditions."""
    assert model.pole_glsl(1 / X) == "(x)"     # the default symbol name
    assert model.pole_glsl(1 / X, "u") == "(u)"
    assert model.pole_glsl(sp.tan(X)) is not None
    assert model.pole_glsl(sp.sin(X)) is None

    assert model.domain_glsl(sp.log(X)) == "(x > 0)"      # default symbol name
    assert model.domain_glsl(sp.log(X), "u") == "(u > 0)"
    assert model.domain_glsl(sp.sqrt(X)) == "(x >= 0)"
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


# --------------------------------------------------------------------------
# Nice ticks
# --------------------------------------------------------------------------


def test_nice_ticks_uses_nice_steps():
    assert nice_ticks(-6.0, 6.0) == [(-6.0, "-6"), (-4.0, "-4"), (-2.0, "-2"), (0.0, "0"),
                                     (2.0, "2"), (4.0, "4"), (6.0, "6")]
    # a step smaller than 1 gets the matching number of decimals in the label
    values = nice_ticks(0.0, 1.0)
    assert [value for value, _ in values] == pytest.approx([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    assert [label for _, label in values] == ["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"]


def test_nice_ticks_covers_the_range_about_target_times():
    for lo, hi in ((-6.0, 6.0), (-2.0, 2.0), (-1e-4, 1e-4), (1000.0, 100000.0), (3.0, 3.7)):
        ticks = nice_ticks(lo, hi, target=8)
        assert all(lo - 1e-9 <= value <= hi + 1e-9 for value, _ in ticks)
        assert 2 <= len(ticks) <= 12
        assert [value for value, _ in ticks] == sorted(value for value, _ in ticks)


def test_nice_ticks_no_minus_zero_and_empty_ranges():
    assert all(label != "-0" for _, label in nice_ticks(-2.0, 2.0))
    assert (0.0, "0.0") in nice_ticks(-0.4, 0.4)
    assert "-0.0" not in [label for _, label in nice_ticks(-0.4, 0.4)]
    assert nice_ticks(5.0, 5.0) == []
    assert nice_ticks(1.0, 0.0) == []


# --------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------

W, H = 900.0, 600.0


def _size(camera: Camera, width: float = W, height: float = H) -> None:
    camera.setViewport(width, height)


def test_the_first_size_shows_the_home_view():
    """The home is a *view* (the classic 12x4 window); the first size report turns it into
    scales, whatever size that report is."""
    for width, height in ((900.0, 600.0), (450.0, 300.0), (1500.0, 700.0)):
        camera = Camera()
        camera.setViewport(width, height)
        xlim, ylim = camera.xlim, camera.ylim
        assert (xlim.x(), xlim.y()) == pytest.approx((-6.0, 6.0))
        assert (ylim.x(), ylim.y()) == pytest.approx((-2.0, 2.0))
        assert camera.zoom == pytest.approx(12.0 / width)
        assert camera.scale_y == pytest.approx(4.0 / height)


def test_resize_keeps_the_scale_and_shows_more_canvas():
    camera = Camera()
    _size(camera)
    zoom, scale_y = camera.zoom, camera.scale_y
    camera.setViewport(1800.0, 600.0)
    assert camera.zoom == zoom                      # nothing zooms while a splitter is dragged
    assert camera.scale_y == scale_y
    assert (camera.xlim.x(), camera.xlim.y()) == pytest.approx((-12.0, 12.0))
    assert (camera.ylim.x(), camera.ylim.y()) == pytest.approx((-2.0, 2.0))


def test_a_configured_camera_is_not_overridden_by_the_first_report():
    camera = Camera()
    camera.zoom = 0.5
    camera.centre = (3.0, 1.0)
    camera.setViewport(W, H)
    assert camera.zoom == 0.5
    assert (camera.centre.x(), camera.centre.y()) == pytest.approx((3.0, 1.0))


def test_zoom_by_is_anchored_and_reversible():
    camera = Camera()
    _size(camera)
    anchor = (0.25, 0.75)
    xlim, ylim = camera.xlim, camera.ylim
    world = (xlim.x() + (xlim.y() - xlim.x()) * anchor[0],
             ylim.y() - (ylim.y() - ylim.x()) * anchor[1])

    camera.zoom_by(120.0, *anchor, W, H)
    new_xlim, new_ylim = camera.xlim, camera.ylim
    assert new_xlim.y() - new_xlim.x() == pytest.approx((xlim.y() - xlim.x()) * 0.9)
    # the world point under the cursor did not move
    moved = (new_xlim.x() + (new_xlim.y() - new_xlim.x()) * anchor[0],
             new_ylim.y() - (new_ylim.y() - new_ylim.x()) * anchor[1])
    assert moved == pytest.approx(world)

    camera.zoom_by(-120.0, *anchor, W, H)
    assert (camera.xlim.x(), camera.xlim.y()) == pytest.approx((xlim.x(), xlim.y()))
    assert (camera.ylim.x(), camera.ylim.y()) == pytest.approx((ylim.x(), ylim.y()))


def test_pan_pixels_is_one_to_one_with_the_cursor():
    camera = Camera()
    _size(camera)
    camera.pan_pixels(-450.0, 300.0)                # half the width right, half the height down
    xlim, ylim = camera.xlim, camera.ylim
    assert (xlim.x(), xlim.y()) == pytest.approx((0.0, 12.0))   # content follows the cursor
    assert (ylim.x(), ylim.y()) == pytest.approx((0.0, 4.0))


def test_toggles_and_zero_deltas_do_nothing():
    camera = Camera()
    _size(camera)
    before = (camera.xlim.x(), camera.xlim.y(), camera.ylim.x(), camera.ylim.y(), camera.zoom)
    camera.zoom_by(0.0, 0.5, 0.5, W, H)
    camera.pan_pixels(0.0, 0.0)
    camera.zoomEnabled = False
    camera.zoom_by(120.0, 0.5, 0.5, W, H)
    camera.panEnabled = False
    camera.pan_pixels(10.0, 10.0)
    assert (camera.xlim.x(), camera.xlim.y(), camera.ylim.x(), camera.ylim.y(),
            camera.zoom) == before
    assert camera.zoomStep == 0.9                   # one notch = 10%
    with pytest.raises(ValueError):
        camera.zoomStep = 1.5


def test_aspect_expands_and_never_crops():
    """A numeric aspect may only *add* canvas: the new view contains the old one."""
    camera = Camera()
    _size(camera)
    x_before, y_before = camera.xlim, camera.ylim
    for aspect in (1.0, 2.0, 0.5, 3.0):
        camera.aspect = aspect
        xlim, ylim = camera.xlim, camera.ylim
        assert xlim.x() <= x_before.x() + 1e-9 and xlim.y() >= x_before.y() - 1e-9
        assert ylim.x() <= y_before.x() + 1e-9 and ylim.y() >= y_before.y() - 1e-9
        # the requested ratio really holds: pixels per y-unit / pixels per x-unit
        assert camera.scale_x / camera.scale_y == pytest.approx(aspect)
    camera.aspect = 1.0
    assert camera.scale_x == pytest.approx(camera.scale_y)      # square units


def test_aspect_auto_keeps_the_current_shape():
    """Switching to "auto" must not jump: the scales stay as they are and simply stop being
    tied to a number, so a resize or a zoom keeps the ratio."""
    camera = Camera()
    _size(camera)
    camera.aspect = 1.0
    scales = (camera.scale_x, camera.scale_y)
    camera.aspect = "auto"
    assert (camera.scale_x, camera.scale_y) == pytest.approx(scales)
    camera.zoom_by(120.0, 0.5, 0.5, W, H)
    assert camera.scale_x / camera.scale_y == pytest.approx(1.0)
    with pytest.raises(ValueError):
        camera.aspect = "square"


def test_xlim_and_ylim_move_the_camera_and_keep_the_ratio():
    camera = Camera()
    _size(camera)
    camera.xlim = (-1.0, 1.0)                       # 6x closer: y follows by the same factor
    assert (camera.xlim.x(), camera.xlim.y()) == pytest.approx((-1.0, 1.0))
    assert (camera.ylim.x(), camera.ylim.y()) == pytest.approx((-2.0 / 6.0, 2.0 / 6.0))
    camera.ylim = (-3.0, 3.0)                       # "auto": the y range is taken as asked
    assert (camera.ylim.x(), camera.ylim.y()) == pytest.approx((-3.0, 3.0))
    assert (camera.xlim.x(), camera.xlim.y()) == pytest.approx((-1.0, 1.0))


def test_reset_returns_to_the_home_view_and_keeps_the_aspect():
    camera = Camera()
    _size(camera)
    camera.aspect = 1.0
    camera.pan_pixels(120.0, 30.0)
    camera.zoom_by(240.0, 0.5, 0.5, W, H)
    camera.reset()
    assert (camera.xlim.x(), camera.xlim.y()) == pytest.approx((-6.0, 6.0))
    assert camera.aspect == 1.0                     # the configuration survives a reset
    assert camera.scale_x == pytest.approx(camera.scale_y)
    assert camera.zoom == pytest.approx(12.0 / W)


def test_ticks_follow_the_view():
    camera = Camera()
    _size(camera)
    ticks_x, ticks_y = camera.tick_values()
    assert [value for value, _ in ticks_x] == pytest.approx([-6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0])
    assert [label for _, label in ticks_y][:2] == ["-2.0", "-1.5"]
    camera.zoom_by(600.0, 0.5, 0.5, W, H)          # 5 notches in: the range shrinks
    assert camera.ticks_x[0][0] > -6.0
    assert camera.ticks_y[-1][0] < 2.0


def test_signals_fire_on_real_changes_only():
    camera = Camera()
    seen = {"view": 0, "ticks": 0, "aspect": 0}
    camera.viewChanged.connect(lambda: seen.__setitem__("view", seen["view"] + 1))
    camera.ticksChanged.connect(lambda: seen.__setitem__("ticks", seen["ticks"] + 1))
    camera.aspectChanged.connect(lambda: seen.__setitem__("aspect", seen["aspect"] + 1))

    _size(camera)
    assert seen == {"view": 1, "ticks": 1, "aspect": 0}
    _size(camera)                                    # same size: nothing to report
    assert seen == {"view": 1, "ticks": 1, "aspect": 0}
    camera.zoom_by(120.0, 0.5, 0.5, W, H)
    assert seen == {"view": 2, "ticks": 2, "aspect": 0}
    camera.aspect = 1.0
    assert seen == {"view": 3, "ticks": 3, "aspect": 1}
    camera.aspect = 1.0                              # idempotent
    assert seen == {"view": 3, "ticks": 3, "aspect": 1}
