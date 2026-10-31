"""Model-layer unit tests: expression -> GLSL, shader sources, camera math, nice ticks.

No Qt widgets and no GPU: the camera is a plain QObject and the tick algorithm is pure, so
the whole view arithmetic is testable without a scene graph.
"""

import pytest
import sympy as sp
from PySide6.QtCore import Qt

from qmlmathplot import Camera, model, nice_ticks

X = sp.Symbol("x")


def test_func_glsl():
    """Small integer powers must be expanded into repeated multiplication (GLSL's
    pow(x, y) is undefined for x<0 and drivers often return NaN); sin/cos must be
    wrapped as SIN/COS (argument reduction, see the note in model)."""
    cases = [
        (X**2, "(x*x)"),
        (X**-2, "(1.0/(x*x))"),
        (X**9, "pow(x, 9.0)"),
        (sp.sin(X) / X, "SIN(x)/x"),
        (sp.exp(-X * X) * sp.sin(10 * X), "exp(-(x*x))*SIN(10*x)"),
    ]
    for expr, expected in cases:
        assert model.func_glsl(expr) == expected, expr


def test_func_glsl_renames_variable():
    assert model.func_glsl(sp.sin(1 / X), "u") == "SIN(1.0/u)"


def test_fractional_powers_use_the_real_branch():
    """``pow`` is undefined for a negative base; odd roots are real there."""
    assert "_real_pow" in model.func_glsl(X ** sp.Rational(1, 3))
    assert "pow(abs(x)" in model.func_glsl(X ** sp.Rational(2, 3))
    assert model.func_glsl(X ** sp.Rational(1, 3)).endswith("0.333333333)")
    assert model.domain_glsl(X ** sp.Rational(1, 3)) is None       # all reals
    assert model.domain_glsl(X ** sp.Rational(2, 3)) is None
    # Float exponents and sqrt take the nonnegative branch.
    assert model.domain_glsl(X**0.5) == "(x >= 0)"
    assert model.domain_glsl(sp.sqrt(X)) == "(x >= 0)"
    assert model.domain_glsl(X ** sp.Rational(-1, 3)) == "(x != 0)"


def test_the_fragment_is_only_the_sampled_fallback():
    """The interval renderer is compute-only; GGB's eligibility rejects floor/sign and
    dependent quotients, so the fragment is the classic sampled plotter counterpart."""
    for expression in (sp.sin(1 / X), sp.floor(X), X / sp.sqrt(X**2)):
        _, fragment = model.shader_sources(expression)
        assert "IVal FI(" not in fragment
        assert "#define MARCH 12" in fragment
        assert "@" not in fragment


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


def test_domain_analysis():
    """Domain conditions (where the function's value is defined at all)."""
    assert model.domain_glsl(sp.log(X)) == "(x > 0)"      # default symbol name
    assert model.domain_glsl(sp.log(X), "u") == "(u > 0)"
    assert model.domain_glsl(sp.sqrt(X)) == "(x >= 0)"
    assert model.domain_glsl(X**2) is None               # the whole domain


def test_shader_sources_inject_domain():
    _, frag = model.shader_sources(1 / X)
    assert "#define DOM(u) ((u != 0))" in frag
    # The sampled fragment is the march: its join invariant (a segment only joins samples
    # within the radius) replaced the fixed-x polyline's POLE guard, so the sampled source
    # no longer defines POLE at all.
    assert "#define MARCH 12" in frag
    assert "#define POLE" not in frag
    _, smooth = model.shader_sources(X**2)
    assert "#define DOM(u) true" in smooth               # not analysable => identically true


def test_unbounded_edges():
    """Which domain boundaries need "infinite extension": only the slowly diverging ones.

    log(x) tends to -inf as x->0+, but the sampling window has a fixed width and cannot
    reach that deep, so in a deep viewport the whole curve would vanish and has to be
    added back by the analytically derived boundary ray. sqrt(x) is bounded at 0 (0), and
    sin(1/x) has no limit at 0, so neither needs it. A boundary that is *also a pole* gets no
    ray either: its samples already exceed any viewport, and the ray would draw exactly the
    connector the pole rules remove — measured, `1/x` grew a pale vertical line at x = ±0.014
    when it did get one (AGENTS item 12).
    """
    assert model.unbounded_edges(sp.log(X)) == [(0.0, True, False)]
    assert model.unbounded_edges(sp.sqrt(X)) == []
    assert model.unbounded_edges(sp.sin(1 / X)) == []
    assert model.unbounded_edges(X**2) == []
    assert model.unbounded_edges(1 / X) == []
    assert model.unbounded_edges(1 / X**2) == []
    # ... while a log-type boundary next to a pole still gets its ray.
    assert [point for point, _down, _up in model.unbounded_edges(sp.log(X) + 1 / (X - 1))] == [0.0]


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
    camera.set_viewport(width, height)


def test_the_first_size_shows_the_home_view():
    """The home is a *view* (the classic 12x4 window); the first size report turns it into
    scales, whatever size that report is."""
    for width, height in ((900.0, 600.0), (450.0, 300.0), (1500.0, 700.0)):
        camera = Camera()
        camera.set_viewport(width, height)
        xlim, ylim = camera.xlim, camera.ylim
        assert (xlim.x(), xlim.y()) == pytest.approx((-6.0, 6.0))
        assert (ylim.x(), ylim.y()) == pytest.approx((-2.0, 2.0))
        assert camera.zoom == pytest.approx(12.0 / width)
        assert camera.scale_y == pytest.approx(4.0 / height)


def test_resize_keeps_the_scale_and_shows_more_canvas():
    camera = Camera()
    _size(camera)
    zoom, scale_y = camera.zoom, camera.scale_y
    camera.set_viewport(1800.0, 600.0)
    assert camera.zoom == zoom                      # nothing zooms while a splitter is dragged
    assert camera.scale_y == scale_y
    assert (camera.xlim.x(), camera.xlim.y()) == pytest.approx((-12.0, 12.0))
    assert (camera.ylim.x(), camera.ylim.y()) == pytest.approx((-2.0, 2.0))


def test_a_configured_camera_is_not_overridden_by_the_first_report():
    camera = Camera()
    camera.zoom = 0.5
    camera.centre = (3.0, 1.0)
    camera.set_viewport(W, H)
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


def test_zoom_at_is_anchored_and_reversible():
    """The pinch primitive: an arbitrary factor, the world point under (u, v) fixed."""
    camera = Camera()
    _size(camera)
    anchor = (0.25, 0.75)
    xlim, ylim = camera.xlim, camera.ylim
    world = (xlim.x() + (xlim.y() - xlim.x()) * anchor[0],
             ylim.y() - (ylim.y() - ylim.x()) * anchor[1])

    camera.zoom_at(0.5, *anchor, W, H)              # twice as wide (zoom out)
    new_xlim, new_ylim = camera.xlim, camera.ylim
    assert new_xlim.y() - new_xlim.x() == pytest.approx((xlim.y() - xlim.x()) * 2.0)
    moved = (new_xlim.x() + (new_xlim.y() - new_xlim.x()) * anchor[0],
             new_ylim.y() - (new_ylim.y() - new_ylim.x()) * anchor[1])
    assert moved == pytest.approx(world)

    camera.zoom_at(2.0, *anchor, W, H)
    assert (camera.xlim.x(), camera.xlim.y()) == pytest.approx((xlim.x(), xlim.y()))
    assert (camera.ylim.x(), camera.ylim.y()) == pytest.approx((ylim.x(), ylim.y()))


def test_shortcut_properties_and_matching():
    """Defaults match real key events (Shift+= reports Key_Plus), and rebinding changes both
    the match and the notify signal QML bindings depend on."""
    camera = Camera()
    bindings = camera.input
    assert bindings.panLeftShortcut == "Left"
    assert bindings.zoomInShortcut == "+|="
    assert bindings.zoomOutShortcut == "-|_"
    assert bindings.matches(Qt.Key_Left.value, 0, bindings.panLeftShortcut)
    assert bindings.matches(Qt.Key_Plus.value, Qt.ShiftModifier.value,
                            bindings.zoomInShortcut)
    assert bindings.matches(Qt.Key_Equal.value, 0, bindings.zoomInShortcut)
    assert bindings.matches(Qt.Key_Minus.value, 0, bindings.zoomOutShortcut)
    assert not bindings.matches(Qt.Key_Right.value, 0, bindings.panLeftShortcut)
    assert not bindings.matches(Qt.Key_Plus.value, 0, "")       # empty disables

    seen = 0

    def note() -> None:
        nonlocal seen
        seen += 1

    bindings.shortcutsChanged.connect(note)
    meta = bindings.metaObject()
    index = meta.indexOfProperty("panLeftShortcut")
    assert index >= 0 and meta.property(index).notifySignal().isValid()
    bindings.panLeftShortcut = "A"
    assert seen == 1 and bindings.panLeftShortcut == "A"
    bindings.panLeftShortcut = "A"                     # idempotent
    assert seen == 1
    assert bindings.matches(Qt.Key_A.value, 0, bindings.panLeftShortcut)
    assert not bindings.matches(Qt.Key_A.value, Qt.ShiftModifier.value,
                                bindings.panLeftShortcut)

    bindings.zoomInShortcut = "Ctrl+="
    assert bindings.matches(Qt.Key_Equal.value, Qt.ControlModifier.value,
                            bindings.zoomInShortcut)
    assert not bindings.matches(Qt.Key_Equal.value, 0, bindings.zoomInShortcut)

    bindings.keyPanStep = 0.25
    assert bindings.keyPanStep == 0.25
    with pytest.raises(ValueError):
        bindings.keyPanStep = 0.0
    with pytest.raises(ValueError):
        camera.animationDuration = -1


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


def test_ticks_are_computed_once_and_only_change_with_the_tick_set():
    """A small pan keeps the same nice numbers: the cache returns the same lists and
    ``ticksChanged`` stays quiet (QML would otherwise rebuild both Repeaters per frame)."""
    camera = Camera()
    _size(camera)
    camera.xlim = (-7.0, 7.0)                                 # ticks sit inside the edges,
    camera.ylim = (-3.0, 3.0)                                 # so a small pan keeps them
    ticks_x, ticks_y = camera.tick_values()
    assert camera.tick_values()[0] is ticks_x                 # one computation, shared
    assert camera.tick_values()[1] is ticks_y

    seen = 0

    def note() -> None:
        nonlocal seen
        seen += 1

    camera.ticksChanged.connect(note)
    camera.pan_pixels(1.0, 0.0)                               # one pixel: same labels
    assert camera.tick_values() == (ticks_x, ticks_y)
    assert seen == 0, "an unchanged tick set must not rebuild the tick models"

    camera.pan_pixels(900.0, 0.0)                             # a whole viewport: new labels
    assert seen == 1
    assert camera.tick_values() != (ticks_x, ticks_y)


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


def test_the_renderer_policy_is_capability_based_not_a_string_list():
    """The compute path is chosen by interval support + topology class, not by spelling.

    `1/x` and `sin(1/x)` are the cases the old `("tan(x)", "log(x)")` whitelist sent to the
    fragment fallback even though their interval topology is well defined.
    """
    from qmlmathplot.rhi import path_compute_supported

    for expression in ("tan(x)", "tan(2*x)", "log(x)", "log(1+x)", "1/x", "sin(1/x)",
                       "x**2", "sin(x)", "x**(1/3)"):
        assert path_compute_supported(sp.sympify(expression)), expression
    # A dependent quotient has no sign pole to justify a gap; the fragment path draws its
    # jump. Operations outside the interval library stay there too.
    for expression in ("x/sqrt(x**2)", "floor(x)", "sign(x)"):
        assert not path_compute_supported(sp.sympify(expression)), expression


def test_the_interval_library_is_trimmed_to_the_functions_the_expression_uses():
    """Every compute shader used to embed the whole library; the trim keeps only the
    dependency closure, which matters for the D3D11 compile at pipeline creation."""
    from qmlmathplot.interval import INTERVAL_LIB, interval_glsl, interval_lib_for

    sine = interval_lib_for(interval_glsl(sp.sin(X)))
    square = interval_lib_for(interval_glsl(sp.Symbol("x") ** 2))
    assert "i_sin(" in sine and "i_tan(" not in sine
    assert "i_pow_int(" in square and "i_sin(" not in square
    assert len(sine) < len(INTERVAL_LIB) // 3


def test_pole_spacing_is_half_the_pole_factor_period():
    """The wrapped-pole fill threshold is derived from the expression, not from a zoom."""
    from qmlmathplot.model import _pole_spacing

    assert _pole_spacing(sp.tan(X)) == pytest.approx(float(sp.pi))
    assert _pole_spacing(sp.tan(2 * X)) == pytest.approx(float(sp.pi) / 2)
    assert _pole_spacing(1 / X) == 0.0          # a single zero: never "dense"
    assert _pole_spacing(sp.exp(sp.tan(X))) == pytest.approx(float(sp.pi))
    assert _pole_spacing(sp.sin(X)) == 0.0      # no pole factor


def test_non_negative_detection():
    """The uniform fill needs a conservative range floor; it must not guess."""
    from qmlmathplot.model import _non_negative

    assert _non_negative(sp.exp(sp.tan(X)))
    assert _non_negative(sp.sqrt(sp.tan(X)))
    assert _non_negative(sp.Abs(sp.tan(X)))
    assert _non_negative(sp.exp(sp.tan(X)) + 1)
    assert not _non_negative(sp.tan(X))
    assert not _non_negative(-sp.exp(sp.tan(X)))


def test_compute_source_accepts_a_parsed_expression(monkeypatch):
    """`Curve` already parsed the expression; the RHI must not sympify the string again."""
    from qmlmathplot.ggb_compute import path_compute_source

    calls: list[object] = []
    real = sp.sympify

    def counting(value, *args, **kwargs):
        calls.append(value)
        return real(value, *args, **kwargs)

    monkeypatch.setattr(sp, "sympify", counting)
    path_compute_source(sp.sin(sp.Symbol("x")))
    assert calls == [], "a parsed Expr must reach the generator without parsing"
