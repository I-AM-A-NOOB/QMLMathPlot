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
from qmlmathplot.curve import Curve, CurveListModel, ImplicitCurve    # ImplicitCurve is new:
# `qmlmathplot/__init__.py` (owned by another workstream) does not re-export it yet, so the tests
# — and the LSP the maintainer wants kept clean — reach it where it lives.

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
    assert "#define FX " in fragment and "#define FY " in fragment
    # the distance to the level set, in pixels, and the same coverage ramp as the 1-D stroke
    assert "float d = abs(f) / max(sqrt(gx * gx + gy * gy), 1e-9);" in fragment
    assert "clamp(lineWidth * 0.5 + 0.5 - d, 0.0, 1.0)" in fragment
    assert "float gx = FX * camera.z;" in fragment and "float gy = FY * camera.w;" in fragment
    # no sampling loop and no envelope band: the minimal single-sheet version
    assert "smp" not in fragment and "band" not in fragment
    assert "isnan(f) || isinf(f)" in fragment               # the non-finite guard


def test_the_implicit_shader_has_the_same_uniform_block_as_the_1d_one() -> None:
    x, y = sp.symbols("x y")
    _, implicit = model.implicit_shader_sources(x**2 + y**2 - 1)
    _, one_d = model.shader_sources(sp.sin(x))
    block = implicit.split("};")[0]
    assert block == one_d.split("};")[0], "the QML side must not need a special case"


# ------------------------------------------------------------------------- rendering
def _render(plot: Plot, app: QGuiApplication) -> QImage:
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
    camera.setViewport(float(WIDTH), float(HEIGHT))
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
def test_implicit_curve_exports_through_the_qt_path(app: QGuiApplication) -> None:
    """The export reuses the same shaders, so an implicit curve needs nothing special there."""
    from PySide6.QtCore import QSize

    plot = _circle_plot()
    image = plot.to_image(xlim=(-2, 2), ylim=(-2, 2), width=300, height=300)
    assert image.size() == QSize(300, 300)
    lit = [y for y in range(image.height()) if _lit(image, image.width() // 2, y)]
    assert lit, "the exported implicit curve is empty"
    assert not _lit(image, image.width() // 2, image.height() // 2), "the middle must be blank"


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
