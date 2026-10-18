"""Implicit curves — the level set ``F(x, y) = 0``: routing, shader text, and the geometry.

The render tests measure the pixels of a unit circle in a ``[-2, 2]²`` view. The centre of the
image must be blank: the 1-D path used to evaluate ``x**2 + y**2 - 1`` as ``y = F(x, y)``, which
is the solution set of ``y² + x² - y - 1 = 0``, a circle centred at ``y = 1/2`` that crosses the
middle row. The stroke must also be the same width at every angle, which that pseudo-conic (with
its varying slope) could not be.
"""

from __future__ import annotations

import math

import pytest
import sympy as sp
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from qmlmathplot import Plot, model, qml_component_path
from qmlmathplot.curve import Curve, CurveListModel, ImplicitCurve, parsed

WIDTH, HEIGHT = 600, 600                 # square, so a circle is round
SPAN = 4.0                               # world units across the view: [-2, 2] in both axes
LIT = 60                                 # colour distance from the white background
CIRCLE = "x**2 + y**2 - 1"


# -------------------------------------------------------------------------- routing
def test_routing_follows_the_free_symbols() -> None:
    """`implicit` is what the artist draws, and it follows the expression: `{x}` is a function of
    x, `{x, y}` is a level set, and the template switches when the expression changes."""
    model_ = CurveListModel()
    assert model_.add_curve("sin(x)").implicit is False
    assert model_.add_curve("0.5").implicit is False       # no free symbol at all
    assert model_.add_curve(CIRCLE).implicit is True
    assert model_.add_curve("x**2 - 1").implicit is False  # only x: a 1-D curve
    assert model_.add_curve(sp.cos(sp.Symbol("x"))).implicit is False
    assert model_.add_curve(sp.Symbol("x") ** 2 + sp.Symbol("y") ** 2 - 1).implicit is True


def test_eq_is_always_a_level_set() -> None:
    """``Eq(lhs, rhs)`` means ``lhs - rhs = 0`` and is an implicit curve even when only x is
    free (``Eq(x**2, 1)`` is two vertical lines, not ``y = x**2 - 1``)."""
    x, y = sp.symbols("x y")
    model_ = CurveListModel()
    circle = model_.add_curve(sp.Eq(x**2 + y**2, 1))
    assert type(circle) is Curve                 # an ordinary curve can re-route later
    assert circle.implicit is True
    assert circle.expr == x**2 + y**2 - 1
    assert circle.expression == "Eq(x**2 + y**2, 1)"
    assert not circle.error
    assert circle.fragmentShader.toString().endswith(".qsb")

    vertical = model_.add_curve(sp.Eq(x**2, 1))
    assert vertical.implicit is True and type(vertical) is Curve
    assert vertical.expr == x**2 - 1

    forced = model_.add_implicit(sp.Eq(x**2 + y**2, 1))
    assert type(forced) is ImplicitCurve and forced.implicit


def test_assigning_an_eq_reroutes_and_can_reroute_back() -> None:
    x, y = sp.symbols("x y")
    model_ = CurveListModel()
    curve = model_.add_curve("sin(x)")
    one_d_shader = curve.fragmentShader
    curve.expression = sp.Eq(x**2 + y**2, 1)
    assert curve.implicit is True
    assert curve.expr == x**2 + y**2 - 1
    assert curve.expression.startswith("Eq(")
    assert curve.fragmentShader != one_d_shader
    curve.expression = "sin(x)"
    assert curve.implicit is False
    assert curve.fragmentShader == one_d_shader


def test_eq_string_form_is_parsed() -> None:
    parsed_text, parsed_expr = parsed("Eq(x**2 + y**2, 1)")
    assert parsed_text == "Eq(x**2 + y**2, 1)"
    x, y = sp.symbols("x y")
    assert parsed_expr == x**2 + y**2 - 1


def test_assigning_an_expression_reroutes_the_same_curve() -> None:
    """The text-field path: what the explorers and `MathPlotWidget.expression` do. A function
    becomes a level set (and back) without replacing the curve."""
    model_ = CurveListModel()
    curve = model_.add_curve("sin(x)")
    one_d_shader = curve.fragmentShader
    curve.expression = CIRCLE
    assert curve.implicit is True and not curve.error
    assert curve.fragmentShader != one_d_shader, "the shader must have been swapped"
    curve.expression = "sin(x)"
    assert curve.implicit is False and curve.fragmentShader == one_d_shader


def test_a_third_symbol_raises() -> None:
    model_ = CurveListModel()
    with pytest.raises(ValueError, match="substituted first"):
        model_.add_curve("a*sin(x)")
    with pytest.raises(ValueError, match="only x"):
        model_.add_curve("x**2 + t**2 - 1")
    assert len(model_) == 0, "a rejected expression must not be added"


def test_add_implicit_is_explicit() -> None:
    """`add_implicit` forces the level-set artist whatever the free symbols — with `x` alone the
    level set is a pair of vertical lines, which the auto-routing would have made 1-D."""
    model_ = CurveListModel()
    vertical = model_.add_implicit("x**2 - 1")
    assert type(vertical) is ImplicitCurve and vertical.implicit
    assert not vertical.error and vertical.fragmentShader.toString().endswith(".qsb")
    x, y = sp.symbols("x y")
    assert type(model_.add_implicit(x**2 + y**2 - 1)) is ImplicitCurve
    assert type(model_.add_curve("x**2 - 1")) is Curve       # the same input, auto-routed
    assert model_.add_curve("x**2 - 1").implicit is False


def test_add_implicit_does_not_parse_an_expression(monkeypatch) -> None:
    calls: list[object] = []
    real = sp.sympify

    def counting(value, *args, **kwargs):
        calls.append(value)
        return real(value, *args, **kwargs)

    monkeypatch.setattr(sp, "sympify", counting)
    x, y = sp.symbols("x y")
    curve = CurveListModel().add_implicit(x**2 + y**2 - 1)
    assert calls == [], "an Expr must reach the artist unconverted"
    assert curve.expression == "x**2 + y**2 - 1"


def test_a_failed_expression_keeps_the_drawn_one() -> None:
    """The old behaviour to never return to: `x**2 + y**2 - 1` drawn as `y = F(x, y)` (a
    pseudo-conic centred at y = 1/2). A template that cannot render the expression reports it and
    keeps the curve that was drawn, while `expression` still shows what was typed."""
    curve = Curve("sin(x)")
    drawn = curve.expr
    curve.expression = "a*sin(x)"
    assert "substitute parameters first" in curve.error
    assert curve.expression == "a*sin(x)" and curve.expr == drawn
    assert curve.implicit is False, "the drawn curve is still the 1-D one"
    curve.expression = "log(x), 1"                        # unparsable
    assert curve.error and curve.expr == drawn


# ------------------------------------------------------------------------- shader text
def test_implicit_shader_uses_the_level_set_formula() -> None:
    x, y = sp.symbols("x y")
    _, fragment = model.implicit_shader_sources(x**2 + y**2 - 1)
    assert "#define F(x, y) " in fragment
    assert "#define FX(x, y) " in fragment and "#define FY(x, y) " in fragment
    assert "#define POLE(x, y) " in fragment
    # the distance to the level set, in pixels, and the same coverage ramp as the 1-D stroke
    assert "float gnorm = max(sqrt(gx * gx + gy * gy), 1e-9);" in fragment
    assert "d = abs(f) / gnorm;" in fragment
    assert "clamp(R - d, 0.0, 1.0)" in fragment
    assert "gx = FX(x, y) * camera.z;" in fragment
    assert "gy = FY(x, y) * camera.w;" in fragment
    # the three general guards: the domain predicate (never evaluate outside), the Newton
    # projection certificate (a converged residual), and the pole factor crossing
    assert "#define DOM(x, y) " in fragment
    assert "bool dom = DOM(x, y);" in fragment
    assert "abs(rf) <= 0.25 * R * rg" in fragment
    assert "POLE(pxp, pyp)" in fragment and "POLE(pxm, pym)" in fragment
    # no sampling loop and no envelope band: the minimal single-sheet version
    assert "smp" not in fragment
    assert "finite_here" in fragment                        # the non-finite guard


def test_a_fractional_root_in_an_implicit_curve_uses_the_real_branch() -> None:
    """The scalar printer emits _real_pow for an odd root; the implicit template must define it."""
    x, y = sp.symbols("x y")
    _, fragment = model.implicit_shader_sources(x ** sp.Rational(1, 3) + y)
    assert "_real_pow" in fragment
    assert "float _real_pow(" in fragment


def test_the_implicit_shader_has_the_same_uniform_block_as_the_1d_one() -> None:
    x, y = sp.symbols("x y")
    _, implicit = model.implicit_shader_sources(x**2 + y**2 - 1)
    _, one_d = model.shader_sources(sp.sin(x))
    block = implicit.split("};")[0]
    assert block == one_d.split("};")[0], "the QML side must not need a special case"


# ------------------------------------------------------------------------- rendering
def _render(plot: Plot, app: QGuiApplication,
            xlim: tuple[float, float] | None = None,
            ylim: tuple[float, float] | None = None) -> QImage:
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(WIDTH, HEIGHT)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    root = view.rootObject()
    root.setProperty("plot", plot)
    view.show()
    if not QTest.qWaitForWindowExposed(view):
        pytest.skip("no usable display/GPU scenegraph")

    camera = plot.camera
    camera.set_viewport(float(WIDTH), float(HEIGHT))
    if xlim is not None and ylim is not None:
        camera.xlim = xlim
        camera.ylim = ylim
    else:
        camera.set_view(0.0, 0.0, SPAN / WIDTH, SPAN / HEIGHT)   # square units: a circle is round
    for _ in range(20):
        app.processEvents()
    result = root.grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    image = result.image()
    view.hide()
    return image


#: The curve colour (#1f77b4) is this far from the white background: a fully covered pixel.
FULL_INK = 435


def _coverage(image: QImage, x: int, y: int) -> float:
    """How much of the pixel the stroke paints, 0..1 (white background, one curve)."""
    color = image.pixelColor(x, y)
    distance = (255 - color.red()) + (255 - color.green()) + (255 - color.blue())
    return min(1.0, distance / FULL_INK)


def _lit(image: QImage, x: int, y: int) -> bool:
    color = image.pixelColor(x, y)
    return (255 - color.red()) + (255 - color.green()) + (255 - color.blue()) > LIT


def _circle_plot() -> Plot:
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.line_width = 2.0
    plot.add_curve(CIRCLE)
    return plot


def _radial_width(image: QImage, degrees: float, radius: float) -> float:
    """The painted stroke width in device pixels along the ray at ``degrees``.

    The ray is radial, i.e. perpendicular to the circle, and the width is the integral of the
    pixel coverage across it — the same area ÷ length measurement AGENTS item 24 uses, so an
    antialiased 3-device-pixel stroke reads 3.0 whatever its ramp does.
    """
    centre_x, centre_y = (image.width() - 1) / 2, (image.height() - 1) / 2
    angle = math.radians(degrees)
    step = 0.1
    total = 0.0
    for index in range(int(radius * 1.6 / step)):
        offset = index * step
        if abs(offset - radius) < radius * 0.2:
            total += _coverage(image, round(centre_x + offset * math.cos(angle)),
                               round(centre_y - offset * math.sin(angle)))
    assert total > 0, f"no stroke along {degrees} degrees"
    return total * step


@pytest.mark.gui
def test_eq_circle_renders_like_the_difference_form(app: QGuiApplication) -> None:
    x, y = sp.symbols("x y")
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.line_width = 2.0
    plot.add_curve(sp.Eq(x**2 + y**2, 1))
    image = _render(plot, app)
    centre_x, centre_y = image.width() // 2, image.height() // 2
    radius = round(image.width() / 4)            # 1 world unit in the [-2, 2] view
    assert _lit(image, centre_x + radius, centre_y), "the Eq circle's right crossing is missing"
    assert _lit(image, centre_x, centre_y + radius), "the Eq circle's bottom crossing is missing"
    assert not any(_lit(image, centre_x + dx, centre_y + dy)
                   for dx in range(-4, 5) for dy in range(-4, 5)), \
        "the middle must stay blank"


@pytest.mark.gui
def test_implicit_circle_is_centred_and_blank_inside(app: QGuiApplication) -> None:
    image = _render(_circle_plot(), app)
    ratio = image.width() / WIDTH
    centre_x, centre_y = image.width() // 2, image.height() // 2

    rows = [y for y in range(image.height()) if _lit(image, centre_x, y)]
    assert rows, "nothing is drawn in the centre column"
    # radius 1 in a [-2, 2] view: the crossings sit a quarter of the height from each edge
    assert min(rows) == pytest.approx(0.25 * image.height(), abs=4 * ratio), \
        f"top crossing at row {min(rows)}, expected {(0.25 * HEIGHT) * ratio:.0f}"
    assert max(rows) == pytest.approx(0.75 * image.height(), abs=4 * ratio), \
        f"bottom crossing at row {max(rows)}, expected {(0.75 * HEIGHT) * ratio:.0f}"
    # the middle of the circle is empty — the 1-D mis-render crossed it (it was centred at y=1/2)
    assert not _lit(image, centre_x, centre_y), "the centre of the circle must be blank"
    assert not any(_lit(image, x, y) for x in range(centre_x - 4, centre_x + 5)
                   for y in range(centre_y - 4, centre_y + 5)), "the middle must stay blank"


@pytest.mark.gui
def test_implicit_circle_stroke_has_the_same_width_at_every_angle(app: QGuiApplication) -> None:
    """The complaint being fixed: the old path drew a pseudo-conic whose stroke varied with the
    slope. A true circle must measure the same everywhere (``lineWidth=2``, so ~3 device px)."""
    image = _render(_circle_plot(), app)
    radius = (WIDTH / SPAN) * (image.width() / WIDTH)        # 1 world unit in device pixels
    angles = (0.0, 45.0, 90.0, 135.0)
    widths = {degrees: _radial_width(image, degrees, radius) for degrees in angles}
    nominal = 2.0 * image.devicePixelRatio()                 # lineWidth in device pixels
    for degrees, width in widths.items():
        assert width == pytest.approx(nominal, abs=0.75), f"{degrees} degrees: {width:.2f} px"
    spread = max(widths.values()) - min(widths.values())
    assert spread <= 0.5, f"the stroke width varies by {spread:.2f} px: {widths}"


@pytest.mark.gui
def test_a_scaled_rational_level_set_does_not_fill_far_fields(app: QGuiApplication) -> None:
    """``(x+y)/(x*y) = 0`` is the line ``y = -x``; the singular-cell disc must not fill the
    far quadrants.

    The singular test originally required only ``|Fx|+|Fy| < 1e-3`` and ``|F| < 0.05``. For this
    expression both hold over huge regions around |x|,|y| ≈ 50: F and its gradient are tiny even
    though the level set is far away. The extra ``|F| < R*|grad F|`` gate is scale-invariant and
    asks the point to be on the set to first order; an isolated point such as ``x²+y²=0`` still
    passes because both sides are zero there.
    """
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.line_width = 2.0
    plot.add_curve("(x+y)/(x*y)")
    image = _render(plot, app, xlim=(-120.0, 120.0), ylim=(-60.0, 60.0))

    def screen(wx: float, wy: float) -> tuple[int, int]:
        return (round((wx + 120.0) / 240.0 * image.width()),
                round((60.0 - wy) / 120.0 * image.height()))

    # Far from y = -x: these were fully filled by the singular-cell disc.
    for wx, wy in ((50.0, 50.0), (-50.0, -50.0), (90.0, 30.0), (-90.0, -30.0)):
        px, py = screen(wx, wy)
        assert not _lit(image, px, py), f"unexpected fill at ({wx}, {wy})"

    # The level set itself is still drawn: (50, -50) is on y = -x.
    px, py = screen(50.0, -50.0)
    assert _lit(image, px, py), "the level set disappeared"


@pytest.mark.gui
def test_an_isolated_singular_point_is_still_drawn(app: QGuiApplication) -> None:
    """``x²+y² = 0`` has no sign change around its only solution; the singular-cell disc draws
    it. The new distance gate must keep this case (both F and its gradient vanish there)."""
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.add_curve("x**2 + y**2")
    image = _render(plot, app)
    assert _lit(image, image.width() // 2, image.height() // 2)


@pytest.mark.gui
def test_implicit_curve_exports_through_the_qt_path(app: QGuiApplication) -> None:
    """The export reuses the same shaders, so an implicit curve needs nothing special there."""
    from PySide6.QtCore import QSize

    plot = _circle_plot()
    image = plot.to_image(xlim=(-2, 2), ylim=(-2, 2), width=300, height=300)
    assert image.size() == QSize(300, 300)
    lit = [y for y in range(image.height()) if _lit(image, image.width() // 2, y)]
    assert lit, "the exported implicit curve is empty"
    assert not _lit(image, image.width() // 2, image.height() // 2), "the middle must be blank"


#: The view the blank exports were reported at, and the device pixel size they asked for.
EXPORT_VIEW = 2.5
EXPORT_SIZE = 400


def _export_plot(expression: str) -> Plot:
    """The reported configuration: a camera told it is 400 logical px wide, showing ±2.5."""
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.line_width = 2.0
    plot.add_curve(expression)
    plot.camera.set_viewport(EXPORT_SIZE, EXPORT_SIZE)
    plot.xlim = (-EXPORT_VIEW, EXPORT_VIEW)
    plot.ylim = (-EXPORT_VIEW, EXPORT_VIEW)
    return plot


def _lit_samples(image: QImage) -> int:
    return sum(1 for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)
               if _lit(image, x, y))


@pytest.mark.gui
def test_the_default_export_range_is_the_camera_view(app: QGuiApplication) -> None:
    """The minimal pair — ``x**2/4 + y**2/3 - 1`` (ellipse) vs ``x**2/4 - y**2/3 - 1``
    (hyperbola), one sign apart — exported with **no explicit range**, the path the reported
    blank image took.

    Root cause, measured: ``export.render`` derived the default range from the offscreen
    widget's *logical* size, ``width / devicePixelRatio``, while the camera's scale is world
    units per logical pixel *of the live view*. At DPR 1.5 a 400x400 device-pixel export is a
    267-logical-pixel widget, so the camera's ±2.5 view came out as ±1.67: the hyperbola's
    entire visible arc (|x| >= 2) fell outside the image (0 lit samples, against 673 for the
    ellipse, which still crosses ±1.67). The shader was never at fault — painting the
    fragment's own ``camera.z`` and ``size.x`` instead of the curve showed 0.0125 and **267**
    for that export. The camera's view cannot depend on how many pixels are asked for: the
    same camera at half the pixels must show the same region, which is also what fails on a
    DPR-1 screen, where the two only coincide at 400x400.
    """
    lit = {}
    for expression in ("x**2/4+y**2/3-1", "x**2/4-y**2/3-1"):
        for size in (EXPORT_SIZE, EXPORT_SIZE // 2):
            image = _export_plot(expression).to_image(width=size, height=size)
            lit[expression, size] = _lit_samples(image)
    # 958 / 461 sampled pixels measured at 400 (the ellipse crosses the smaller range too, so
    # it was never blank — the control of the pair), 480 / 233 at 200.
    assert lit["x**2/4+y**2/3-1", EXPORT_SIZE] > 400, lit
    assert lit["x**2/4-y**2/3-1", EXPORT_SIZE] > 200, lit
    assert lit["x**2/4+y**2/3-1", EXPORT_SIZE // 2] > 100, lit
    assert lit["x**2/4-y**2/3-1", EXPORT_SIZE // 2] > 100, lit


@pytest.mark.gui
def test_implicit_circle_reaches_its_radius(app: QGuiApplication) -> None:
    """The crossings are where the geometry says: radius 1 in a [-2, 2] view, both axes."""
    image = _render(_circle_plot(), app)
    ratio = image.width() / WIDTH
    radius = WIDTH / SPAN * ratio                            # 1 world unit in device pixels
    centre_x, centre_y = image.width() / 2, image.height() / 2
    columns = [x for x in range(image.width()) if any(_lit(image, x, y) for y in range(image.height()))]
    rows = [y for y in range(image.height()) if any(_lit(image, x, y) for x in range(image.width()))]
    assert centre_x - min(columns) == pytest.approx(radius, abs=4 * ratio)
    assert max(columns) - centre_x == pytest.approx(radius, abs=4 * ratio)
    assert centre_y - min(rows) == pytest.approx(radius, abs=4 * ratio)
    assert max(rows) - centre_y == pytest.approx(radius, abs=4 * ratio)


@pytest.mark.gui
def test_a_partial_that_is_not_a_single_term_still_strokes(app: QGuiApplication) -> None:
    """d/dx (x-1)**2 = 2*x - 2 is a *sum*, so an unparenthesized ``FX`` macro splices into
    ``gx = FX * camera.z`` as ``2*x - 2 * camera.z``: the gradient loses the per-pixel scale and
    the coverage becomes ``|f|/2``, which lights the whole disc (measured 16221 of ~40000
    sampled pixels, i.e. a disc of radius ~2 instead of the radius-1 ring). A real stroke covers
    the circumference times the width — about 1 % of the image — so a few percent is the line
    between a stroke and a fill.
    """
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.line_width = 2.0
    plot.add_curve("(x-1)**2+y**2-1")          # centred at (1, 0), radius 1
    image = _render(plot, app)

    lit = sum(1 for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)
              if _lit(image, x, y))
    total = (image.width() // 2 + 1) * (image.height() // 2 + 1)
    assert lit > 0, "nothing is drawn"
    assert lit < 0.05 * total, f"{lit}/{total} pixels lit: the disc filled instead of stroking"


@pytest.mark.gui
def test_pole_of_tan_does_not_bridge_the_branches(app: QGuiApplication) -> None:
    """x - tan(y) = 0 is the family y = atan(x) + k*pi; between the branches tan(y) jumps
    over ±inf. The first-order distance |F|/|grad F| degenerates at that jump (the gradient
    explodes, the quotient collapses to zero) and the pole's whole neighbourhood comes out
    "on the set" — measured: a full-width line at every y = pi/2 + k*pi. The
    linearisation-validity probes see the discontinuity (their second difference is of the
    order of the change itself) and drop it.
    """
    import math

    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.add_curve("x-tan(y)")
    image = _render(plot, app, xlim=(-1.6, 1.6), ylim=(-4.0, 4.0))
    width, height = image.width(), image.height()
    for pole in (-math.pi / 2, math.pi / 2):
        row = int((4.0 - pole) / 8.0 * (height - 1))
        neighbourhood = sum(_lit(image, x, y)
                            for y in range(row - 4, row + 5)
                            for x in range(0, width, 4))
        assert neighbourhood == 0, \
            f"the pole at y={pole:+.3f} drew a connector: {neighbourhood} lit pixels"


@pytest.mark.gui
def test_domain_boundary_draws_no_garbage_line(app: QGuiApplication) -> None:
    """y = log(1+x) undefined for x <= -1: an *evaluated* log(negative) returns finite
    garbage on this driver, and the garbage paints the boundary as a vertical line. The
    injected domain predicate guards before evaluating, and the stroke's outer half (one
    radius past the boundary) is the only thing allowed outside it.
    """
    import math

    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.add_curve("y-log(1+x)")
    image = _render(plot, app, xlim=(-7.7592, 5.2778), ylim=(-9.3432, 0.7309))
    width, height = image.width(), image.height()
    # every column x <= -1 must be completely dark (the level set lives at x > -1)
    boundary = int((-1.0 + 7.7592) / 13.037 * (width - 1))
    for col in range(0, boundary - 3):
        assert not any(_lit(image, col, y) for y in range(height)), \
            f"a garbage line at column {col} (x = {-7.7592 + col / width * 13.037:+.4f})"
    # A stroke is a band around the level set, so the half lying outside the domain is
    # painted too (the distance is extrapolated from the nearest in-domain point); only a
    # *garbage line* farther out would be a bug. One stroke radius is the allowance.
    lit_cols = [x for x in range(width) if any(_lit(image, x, y) for y in range(height))]
    assert lit_cols, "nothing is drawn"
    assert lit_cols[0] >= boundary - 3, \
        f"a garbage line at column {lit_cols[0]}, far left of the boundary at {boundary}"
    # the other end is where the geometry says: x = e^0.73 - 1
    true_end = int((math.exp(0.7309) - 1 + 7.7592) / 13.037 * (width - 1))
    assert abs(lit_cols[-1] - true_end) <= 6, \
        f"the curve ends at column {lit_cols[-1]}, expected {true_end}"


@pytest.mark.gui
def test_tan_pole_has_no_connector_in_a_wide_y_view(app: QGuiApplication) -> None:
    """A simple pole is monotone across the jump, so the second-difference test can pass and
    |F|/|grad F| becomes a pseudo-zero. Measured before the pole-factor guard: x - tan(y)
    painted a horizontal connector through y = pi/2 + k*pi once the y view was wider than
    ~12 units (the band scales with the y scale, which is why the old ±4-row test at y∈[-4,4]
    passed). At the pole row the true level set is at x = ±inf, so the whole row is dark.
    """
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.add_curve("x-tan(y)")
    image = _render(plot, app, xlim=(-6.0, 6.0), ylim=(-10.0, 10.0))
    width, height = image.width(), image.height()
    for pole in (-math.pi / 2, math.pi / 2):
        row = int((10.0 - pole) / 20.0 * (height - 1))
        neighbourhood = sum(_lit(image, x, y)
                            for y in range(row - 2, row + 3)
                            for x in range(width))
        assert neighbourhood == 0, \
            f"the pole at y={pole:+.3f} drew {neighbourhood} connector pixels"


@pytest.mark.gui
def test_tan_has_no_far_pixels_in_a_wide_y_view(app: QGuiApplication) -> None:
    """The pseudo-zero is not confined to the pole row: in a y-view of ±20 the old
    first-order distance/validity pair accepted points up to ~143 px away from the true
    branch (the scan found them near the view edges). Every lit pixel must be within a
    couple of stroke widths of the level set x = tan(y), i.e. y = atan(x) + k*pi.
    """
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.add_curve("x-tan(y)")
    image = _render(plot, app, xlim=(-6.0, 6.0), ylim=(-20.0, 20.0))
    width, height = image.width(), image.height()
    y_scale = 40.0 / height
    far = 0
    for row in range(0, height, 2):
        world_y = 20.0 - (row + 0.5) / height * 40.0
        for column in range(0, width, 2):
            if not _lit(image, column, row):
                continue
            world_x = -6.0 + (column + 0.5) / width * 12.0
            base = math.atan(world_x)
            distance = min(abs(world_y - (base + k * math.pi)) for k in range(-8, 9))
            if distance / y_scale > 4.0:
                far += 1
    assert far == 0, f"{far} lit pixels are farther than 4 px from the level set"


@pytest.mark.gui
def test_a_domain_boundary_keeps_the_stroke_on_both_sides(app: QGuiApplication) -> None:
    """x = log(y-1) approaches y = 1 from above; at x = -5 the visible curve is ~1 px above
    the boundary, i.e. inside its own stroke radius. Clipping the stroke to the domain (the
    old early return and two-sided probe) left only its upper half — the curve appeared to
    thin out and then vanish. The stroke is a band around the level set: both sides paint.
    """
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.add_curve("x-log(y-1)")
    image = _render(plot, app, xlim=(-6.0, 6.0), ylim=(-2.0, 2.0))
    width, height = image.width(), image.height()
    column = int((-5.0 + 6.0) / 12.0 * (width - 1))
    boundary_row = int((2.0 - 1.0) / 4.0 * (height - 1))
    assert _lit(image, column, boundary_row - 1), "the stroke is missing above the boundary"
    assert _lit(image, column, boundary_row), "the stroke is missing below the boundary"
    lit_rows = sum(_lit(image, column, y) for y in range(height))
    assert lit_rows < 0.1 * height, f"the boundary column filled in: {lit_rows} rows"


@pytest.mark.gui
def test_the_deep_log_descent_reaches_the_boundary(app: QGuiApplication) -> None:
    """y = log(x) hugs x = 0 in a deep view. The level set is at x > 0 and its stroke's left
    half is outside the domain; dropping out-of-domain pixels removed that half and the
    descent disappeared as it approached the axis. Both halves must be painted to the bottom.
    """
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.add_curve("y-log(x)")
    image = _render(plot, app, xlim=(-0.001, 0.001), ylim=(-16.0, -13.0))
    width, height = image.width(), image.height()
    row = height - 5
    columns = [x for x in range(width) if _lit(image, x, row)]
    assert columns, "the descent disappears at the domain boundary"
    centre = width // 2
    assert any(x < centre for x in columns) and any(x >= centre for x in columns), \
        f"the stroke does not straddle x=0: lit columns {columns}"
