"""End-to-end smoke tests: really open a window and render, then check pixels for the two
pieces of the puzzle (screen-space stroke + undersampled envelope band) and for the QML
furniture (grid, ticks, title).

Grabbing the image must use the asynchronous ``QQuickItem.grabToImage()``: the synchronous
handshake of ``QQuickWindow.grabWindow()`` deadlocks with the Python-side scene-graph
objects (the window shows up as "not responding").

The backend is chosen by the ``QSG_RHI_BACKEND`` environment variable (unset = Qt default).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickItem, QQuickView
from PySide6.QtTest import QTest

from qmlmathplot import Plot, qml_component_path

pytestmark = pytest.mark.gui

WIDTH, HEIGHT = 900, 600
BACKGROUND = (0xFF, 0xFF, 0xFF)  # the plot background (white: matplotlib's default)
HOME = (-6.0, 6.0, -2.0, 2.0)    # the classic view: the pixel positions below assume it


def _render_plot(app: QGuiApplication, expression: str | None = None,
                 pan_pixels: float = 0.0, zoom_steps: int = 0, furniture: bool = False,
                 title: str = "", xlim: tuple[float, float] | None = None,
                 ylim: tuple[float, float] | None = None):
    """Open the component at the classic 900x600 view and grab one frame: (image, plot)."""
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(WIDTH, HEIGHT)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]

    root = view.rootObject()
    if expression is None:
        # No model injection: the standalone component creates its own Plot. A curve is now
        # explicit (`defaultExpression`), not a hidden sin(x).
        root.setProperty("defaultExpression", "sin(x)")
        plot = None
    else:
        # MVVM: the app side creates the model and injects it into the component
        plot = Plot()
        plot.add_curve(expression)
        root.setProperty("plot", plot)
    if plot is not None:
        # The pixel expectations below measure the *curve*, and the furniture sits exactly on
        # the columns being measured (a grid line, the axis and its labels), so it is switched
        # on only for the test that is about the furniture itself.
        plot.grid = furniture
        plot.ticks_visible = furniture
        plot.title = title

    view.show()
    if not QTest.qWaitForWindowExposed(view):
        pytest.skip("no usable display/GPU scenegraph")

    if plot is not None:
        camera = plot.camera
        camera.set_viewport(float(WIDTH), float(HEIGHT))
        camera.xlim = xlim if xlim is not None else (HOME[0], HOME[1])
        camera.ylim = ylim if ylim is not None else (HOME[2], HOME[3])
        if pan_pixels:
            camera.pan_pixels(0.0, pan_pixels)
        if zoom_steps:
            for _ in range(zoom_steps):
                camera.zoom_by(120.0, 0.5, 0.5, float(WIDTH), float(HEIGHT))  # each step spans x0.9

    for _ in range(20):
        app.processEvents()
    result = root.grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    image = result.image()
    view.hide()
    return image, plot


def _render(app: QGuiApplication, expression: str | None = None,
            pan_pixels: float = 0.0, zoom_steps: int = 0) -> QImage:
    return _render_plot(app, expression, pan_pixels, zoom_steps)[0]


def _lit(image: QImage, x: int, y: int, threshold: int = 20) -> bool:
    color = image.pixelColor(x, y)
    return (abs(color.red() - BACKGROUND[0]) + abs(color.green() - BACKGROUND[1])
            + abs(color.blue() - BACKGROUND[2])) > threshold


def _dark(image: QImage, x: int, y: int) -> bool:
    """Whether a pixel is far darker than the background (an axis line, not a grid line)."""
    color = image.pixelColor(x, y)
    return max(BACKGROUND[0] - color.red(), BACKGROUND[1] - color.green(),
               BACKGROUND[2] - color.blue()) > 120


def _lit_count(image: QImage) -> int:
    return sum(_lit(image, x, y) for y in range(image.height()) for x in range(image.width()))


def _curve_count(image: QImage) -> int:
    """Pixels whose hue is the default curve colour (blue far above red).

    ``_lit`` also sees the grid and the tick labels, so the standalone test needs a count
    that only the curve can satisfy.
    """
    count = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            if color.blue() - color.red() > 60:
                count += 1
    return count


def _column_ratio(image: QImage, x: int) -> float:
    """Fraction of lit pixels in one column (independent of devicePixelRatio)."""
    return sum(_lit(image, x, y) for y in range(image.height())) / image.height()


def _center_column_ratio(image: QImage) -> float:
    """Fraction of lit pixels in the image's centre column (world x≈0, the
    singularity of sin(1/x))."""
    return _column_ratio(image, image.width() // 2)


def test_smooth_curve_is_a_thin_stroke(app: QGuiApplication) -> None:
    image = _render(app, "sin(x)")
    width, height = image.width(), image.height()
    assert _lit_count(image) > 100, "nothing at all is drawn on the curve"
    assert _lit(image, width // 2, height // 2), "sin(0)=0 should pass through the view centre"
    assert not _lit(image, width // 2, height // 10), "above the curve should be background"
    ratio = _center_column_ratio(image)
    assert ratio < 0.05, f"the centre column should hold only a thin line, got {ratio:.3f}"


def test_component_works_without_injection(app: QGuiApplication) -> None:
    """Without an injected model the component brings its own plot; an explicit
    ``defaultExpression`` adds the standalone curve (there is no hidden default)."""
    image = _render(app)
    assert _curve_count(image) > 100, "the explicit default sin(x) curve should draw"


def test_no_hidden_curve_without_default_expression(app: QGuiApplication) -> None:
    """A bare PlotView creates an empty Plot; the curve used to be an implicit sin(x)."""
    view = QQuickView()
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    root = view.rootObject()
    app.processEvents()
    curves = root.property("curves")        # QtObject-typed forwarder: the Python model
    assert curves is not None
    assert curves.rowCount() == 0, "PlotView added a curve nobody asked for"
    view.hide()


def _solid_outside_pm1(image: QImage) -> int:
    """Number of **solid** pixels inside the central 41 columns that fall outside |y|>1
    (threshold taken high, so antialiasing feathering is not counted).

    sin(1/x) has range ±1; solid pixels beyond that are artifacts.
    """
    height = image.height()
    columns = range(image.width() // 2 - 20, image.width() // 2 + 21)
    rows = list(range(0, int(height * 0.25))) + list(range(int(height * 0.75), height))
    return sum(_lit(image, x, y, threshold=150) for x in columns for y in rows)


def _solid_band_columns(image: QImage) -> int:
    """Number of columns of the solid band (each column >40% lit), to keep an eye on
    "don't widen the band just to smooth out the jaggies"."""
    height = image.height()
    return sum(1 for x in range(image.width())
               if sum(_lit(image, x, y, threshold=60) for y in range(height)) > 0.4 * height)


def test_undersampled_column_fills_envelope_within_pm1(app: QGuiApplication) -> None:
    """The singular column of sin(1/x) fills the true ±1 envelope (about half a column),
    must not be painted outside ±1, and its width must not run out of control.

    The sampling window is deliberately wider than strictly needed so the comb-like
    aliasing is smoothed out, at the cost of a slightly wider fill; this tradeoff is intended.
    """
    image = _render(app, "sin(1/x)")
    ratio = _center_column_ratio(image)
    assert 0.3 < ratio < 0.7, f"the singular column should fill the ±1 envelope (about half a column), got {ratio:.2f}"
    outside = _solid_outside_pm1(image)
    # the ±1 band's own stroke extends half a line width outside it
    assert outside < 90, f"sin(1/x) has range ±1, so there should be no solid pixels outside it, got {outside}"
    width = _solid_band_columns(image) / 1.5  # the grab carries devicePixelRatio
    assert 5 < width < 60, f"the solid band width should be a single-digit to a few dozen logical columns, got {width:.0f}"

    # The band is the interval image of the pixel's column, not a ±8-column sample window.
    # Before: at x=0.0844 the true range was [-0.968, -0.066], the band painted
    # [-1.007, 0.944] (1.66 world units of overfill). The interval column is exact, so the
    # overfill is only the stroke/AA radius.
    width, height = image.width(), image.height()
    dx = 12.0 / 900.0                     # one logical column, the shader's dx
    worst = 0.0
    for x in range(0, width, 3):
        world_x = -6.0 + (x + 0.5) / width * 12.0
        if not 0.05 < abs(world_x) < 0.2:
            continue
        xs = np.linspace(world_x - dx / 2, world_x + dx / 2, 4001)
        ys = np.sin(1.0 / xs)
        rows = [y for y in range(height) if _lit(image, x, y)]
        if not rows:
            continue
        low = 2.0 - (max(rows) + 0.5) / height * 4.0
        high = 2.0 - (min(rows) + 0.5) / height * 4.0
        worst = max(worst, low - float(ys.min()), float(ys.max()) - high)
    assert worst < 0.05, f"the transition band overfills by {worst:.3f} world units"


@pytest.mark.gui
def test_fractional_power_draws_the_negative_branch(app: QGuiApplication) -> None:
    """x**(1/3) is real for x<0; GLSL pow() is not, so the scalar branch prints _real_pow."""
    image = _render(app, "x**(1/3)")
    width, height = image.width(), image.height()
    left = sum(_lit(image, x, y)
               for x in range(0, width // 2 - 8, 2) for y in range(0, height, 2))
    right = sum(_lit(image, x, y)
                for x in range(width // 2 + 8, width, 2) for y in range(0, height, 2))
    assert left > 100 and right > 100, f"one branch is missing: left={left}, right={right}"


def _column_lit_at(image: QImage, x: int) -> int:
    """Number of lit pixels in the pixel column ``x``."""
    x = min(image.width() - 1, max(0, x))
    return sum(_lit(image, x, y) for y in range(image.height()))


def _column_lit(image: QImage, world_x: float) -> int:
    """Number of lit pixels in the column at the given world x."""
    return _column_lit_at(image, int((world_x + 6) / 12 * image.width()))


def test_asymptote_is_not_connected(app: QGuiApplication) -> None:
    """1/x and tan(x) must not draw a vertical connecting line at their poles
    (a jump over a ±inf range).

    Every pole in the view is checked, and a small neighbourhood rather than the pole's own
    column: the connector lands on whichever column straddles the pole, which need not be
    the pole's column (measured before the fix: full-height columns at x = ±0.02 for 1/x and
    at x ≈ -1.55 and ±4.7 for tan(x), while the single column the earlier version checked was
    blank — it passed by luck).
    """
    for expr, poles in (("1/x", [0.0]),
                        ("tan(x)", [-4.7124, -1.5708, 1.5708, 4.7124]),
                        ("exp(tan(x))", [-4.7124, -1.5708, 1.5708, 4.7124])):
        image = _render(app, expr)
        height = image.height()
        for pole in poles:
            center = int((pole + 6) / 12 * image.width())
            lit = max(_column_lit_at(image, x) for x in range(center - 3, center + 4))
            assert lit < 0.25 * height, \
                f"{expr} draws a connecting line at x={pole}: {lit} lit pixels"


def test_jump_discontinuity_is_not_connected(app: QGuiApplication) -> None:
    """A plain jump discontinuity must not draw a vertical connector.

    x/sqrt(x^2) is sign(x): it jumps 2 world units at x=0 and has *no pole denominator*
    for the old analytic guards to recognise. The join invariant (a segment only ever
    joins samples within 2.5 px of each other) refuses the chord because it is two
    viewports long — measured before the invariant: a 454-px column at x=0.
    """
    image = _render(app, "x/sqrt(x**2)")
    height = image.height()
    center = image.width() // 2
    worst = max(_column_lit_at(image, x) for x in range(center - 3, center + 4))
    assert worst < 0.25 * height, \
        f"the jump is drawn as a connector: {worst} lit pixels at x=0"


def test_steep_slope_stroke_is_continuous_and_constant_width(app: QGuiApplication) -> None:
    """k*x from mild to extreme slope: the stroke keeps one width and no breaks.

    The march's arc-length sampling makes the width a property of the line, not of the
    slope (the old fixed-x renderer lost ~10 % at slope 200, where its ±1-column window
    fought the band criteria). A march that ran out of budget before reaching its own
    foot used to dash the line instead — the chord of one budget's length per column.
    """
    widths = []
    for k in (2, 10, 40, 200):
        image = _render(app, f"{k}*x")
        chord_length = math.hypot(2 * (2.0 / k) * image.width() / 12.0,
                                  4.0 * image.height() / 4.0)
        widths.append(_curve_count(image) / chord_length)
        # the stroke may appear dashed only if some column's lit rows split in two
        for col in range(image.width()):
            ys = [y for y in range(image.height()) if _lit(image, col, y)]
            if len(ys) < 4:
                continue
            best = cur = 1
            for i in range(1, len(ys)):
                cur = cur + 1 if ys[i] == ys[i - 1] + 1 else 1
                best = max(best, cur)
            assert best > 0.7 * len(ys), \
                f"slope {k}: column {col} dashes the line (longest run {best} of {len(ys)})"
    assert all(0.85 * widths[0] <= w <= 1.15 * widths[0] for w in widths[1:]), \
        f"the stroke width must not depend on the slope: {[round(w, 2) for w in widths]}"


def test_steep_wall_at_large_y_is_drawn_and_keeps_its_width(app: QGuiApplication) -> None:
    """1/x and tan(x) at large |y| with a wide x view: each branch is a near-vertical wall.

    Two regressions share the same render: the wall used to vanish entirely (its pixel's foot
    is a whole view up the arc, beyond any march budget), and once drawn it was only two
    columns wide instead of three. The interval path's chord/segment stroke supplies both.
    """
    for expr in ("1/x", "tan(x)"):
        image, _ = _render_plot(app, expr, xlim=(-6.0, 6.0), ylim=(50.0, 60.0))
        height = image.height()
        tallest = max(_column_lit_at(image, x) for x in range(image.width()))
        assert tallest >= 0.9 * height, \
            f"{expr} at large |y|: tallest column only {tallest} of {height} rows"
        total = sum(_column_lit_at(image, x) for x in range(image.width())) / height
        assert total > 2.5, \
            f"{expr} at large |y|: the wall is only {total:.2f} columns wide"


def test_shifted_log_descent_wall_is_continuous(app: QGuiApplication) -> None:
    """log(1+x) deep in its descent: the wall must not break in the middle.
    Three separate holes used to open in it: (a) out-of-domain pixels just left of the
    boundary took the early return with the ray alone, and the ray started at a single
    close probe — everything between the polyline's reach and that start was dark;
    (b) the wide window's samples straddled the boundary, where GLSL's log(negative)
    is undefined and the driver returned finite garbage — the band collapsed to zero
    for a whole column; (c) the wide window's t-indexed loop form computed
    out-of-envelope samples under this driver's optimiser even guarded. Measured on
    this view before: a ~180-row hole at x ≈ −1; after: 5 rows, the ray start's own
    antialiasing.
    """
    image, _ = _render_plot(app, "log(1+x)", xlim=(-2.91528, 0.695638),
                            ylim=(-7.67519, -4.78814))
    height = image.height()
    worst = 0
    for col in range(image.width()):
        ys = [y for y in range(height) if _lit(image, col, y)]
        if not ys:
            continue
        for i in range(1, len(ys)):
            if ys[i] == ys[i - 1] + 1:
                continue
            worst = max(worst, ys[i] - ys[i - 1] - 1)
    assert worst <= 12, f"the descent wall breaks: a {worst}-row gap in a column"


def test_descent_is_one_wall_not_two(app: QGuiApplication) -> None:
    """A shallower descent view must not fork into two parallel walls.

    When the curve's own samples already reach the viewport's bottom, the boundary ray
    is redundant — and drawing it next to the visible curve reads as a fork (measured:
    the ray at x = −1 beside the curve at x ≈ −0.996 in a 0.9-unit-wide view of
    log(1+x)). The gate is the samples' deepest point; the ray's *top edge* is a
    separate, higher quantity, and conflating them was the fork.
    """
    image, _ = _render_plot(app, "log(1+x)", xlim=(-1.5912, -0.4357),
                            ylim=(-6.0641, -5.1712))
    width = image.width()
    groups = 0
    prev_lit = False
    for col in range(width):
        lit = any(_lit(image, col, y) for y in range(image.height()))
        if lit and not prev_lit:
            groups += 1
        prev_lit = lit
    assert groups == 1, f"the descent forks into {groups} walls"


def test_out_of_domain_is_blank(app: QGuiApplication) -> None:
    """log(x), sqrt(x) and asin(x) draw nothing outside their domain (x<0 / |x|>1)."""
    for expr in ("log(x)", "sqrt(x)"):
        image = _render(app, expr)
        left = sum(_lit(image, x, y) for x in range(0, image.width() // 2 - 4)
                   for y in range(0, image.height(), 5))
        assert left == 0, f"{expr} should have no pixels for x<0, got {left}"
    image = _render(app, "asin(x)")
    # left end x in [-6, -1]: everything with |x|>1 lies outside the domain
    far = sum(_lit(image, x, y)
              for x in range(0, int((-1.0 + 6) / 12 * image.width()) - 8)
              for y in range(0, image.height(), 5))
    assert far == 0, f"asin(x) should have no pixels for |x|>1, got {far}"


def test_log_descent_is_not_cut_and_keeps_its_width(app: QGuiApplication) -> None:
    """log(x) must be drawn all the way out of the viewport as x→0+ and stay as wide as any
    other stroke (not "gradually thin away" or pinch to a hairline).

    The viewport is panned down by 6 units (y in [-8,-4]): the curve's column at x≈0.004
    sweeps from -4.5 to -inf within that single column, so lit pixels must land near the
    bottom edge. The width was 1.00-1.67 of the nominal 3.00 device px before the boundary
    stroke was widened; it is 3.00-3.23 now.
    """
    image, plot = _render_plot(app, "log(x)", pan_pixels=-900.0)
    height = image.height()
    column = _column_lit(image, 0.004)
    assert column > 0, "log(x) should have pixels at x≈0.004"
    center = image.width() // 2
    bottom = sum(_lit(image, x, y) for x in range(center - 4, center + 5)
                 for y in range(int(height * 0.97), height))
    assert bottom > 0, "log(x)'s descending part should be drawn all the way to the viewport's bottom edge"

    nominal = plot.line_width * image.devicePixelRatio()
    widths = [_coverage_sum(image, row, center)
              for row in range(image.height() // 2, image.height(), 60)]
    assert min(widths) >= 0.95 * nominal, f"descent pinched to {min(widths):.2f} of {nominal:.2f} px"
    assert max(widths) <= 1.5 * nominal, f"descent too fat: {max(widths):.2f} of {nominal:.2f} px"


def test_log_descends_into_deep_views(app: QGuiApplication) -> None:
    """log(x)'s descending part must also be visible in deep viewports (x→0+ diverges
    slowly, so the sampling window cannot reach it).

    With the viewport panned to y in [-16,-12], the visible curve lies entirely in
    x in [1e-7, 6e-6], hugging the domain boundary x=0; a fixed-width sampling window can
    only reach log≈-7, so the whole segment would disappear. It is drawn by the "unbounded
    boundary ray" along x=0, which shows up as a full column of vertical descent hugging
    the y axis.
    """
    image = _render(app, "log(x)", pan_pixels=-2100.0)
    height = image.height()
    center = image.width() // 2
    col = sum(_lit(image, x, y) for x in range(center - 3, center + 4)
              for y in range(height))
    assert col > 0.5 * height, f"in a deep viewport log(x) should have a full column of descent hugging x=0, got {col} pixels"


#: Full ink of the curve colour (#1f77b4) on the white background, summed over RGB.
FULL_INK = 435.0


def _coverage_sum(image: QImage, row: int, center: int, half: int = 12) -> float:
    """Total ink in a row window, i.e. the width of a nearly vertical stroke, in device px."""
    total = 0.0
    for x in range(max(0, center - half), min(image.width(), center + half + 1)):
        color = image.pixelColor(x, row)
        total += min(1.0, ((255 - color.red()) + (255 - color.green())
                           + (255 - color.blue())) / FULL_INK)
    return total



def test_a_shifted_log_does_not_draw_a_line_at_its_boundary(app: QGuiApplication) -> None:
    """`log(x-1)` and friends must not grow a full-height line at their boundary.

    The boundary moves with the shift, and the pixels just *outside* the domain have no valid
    sample at all — so the ray's "is it needed?" test cannot read it off their samples. Judging
    it by the sentinel drew a full-height column at x = 1 in the classic view (measured: two
    columns of 900 px), where the visible curve is nowhere near the boundary. The extent is now
    probed just inside the domain instead.
    """
    for expr, boundary in (("log(x-1)", 1.0), ("log(x+2)", -2.0)):
        image = _render(app, expr)
        height = image.height()
        center = int((boundary + 6) / 12 * image.width())
        tallest = max(_column_lit_at(image, x) for x in range(center - 3, center + 4))
        assert tallest < 0.25 * height, \
            f"{expr} draws a line at its boundary x={boundary}: {tallest} lit pixels"


def test_extreme_zoom_of_oscillation_fills_solid(app: QGuiApplication) -> None:
    """Extreme zoom on sin(1/x): once the oscillation is far faster than the sampling rate
    the screen should fill solid, not be a screen full of vertical stripes.

    The band is gated by a criterion expressed in view-span units rather than pixels: a
    pixel-based gate would switch the band off entirely once a zoomed view spans less than
    the value range (in pixels), leaving only jagged polylines.
    """
    image = _render(app, "sin(1/x)", zoom_steps=90)
    height = image.height()
    counts = [sum(_lit(image, x, y) for y in range(height)) for x in range(image.width())]
    filled = sum(1 for c in counts if c > 0.5 * height) / len(counts)
    assert filled > 0.5, f"extreme zoom should fill solid (sub-pixel oscillation), got a filled-column ratio of {filled:.1%}"


def test_steep_segments_are_not_fragmented(app: QGuiApplication) -> None:
    """Steep segments must not be cut off as jumps by a "drop threshold" (after zooming, a
    vertical stripe must be one continuous line).

    Poles are already handled by the analytic gap, so no jump criterion is applied here.
    """
    image = _render(app, "sin(1/x)", zoom_steps=30)
    height = image.height()
    worst = 1.0
    for x in range(image.width()):
        ys = [y for y in range(height) if _lit(image, x, y)]
        if not ys:
            continue
        best = cur = 1
        for i in range(1, len(ys)):
            cur = cur + 1 if ys[i] == ys[i - 1] + 1 else 1
            best = max(best, cur)
        worst = min(worst, best / len(ys))
    assert worst > 0.9, f"vertical stripes should be continuous (longest run / lit rows), worst column only {worst:.2f}"


def test_custom_ticks_and_legend_reach_qml(app: QGuiApplication) -> None:
    plot = Plot()
    plot.axes_position = "box"
    plot.legend = True
    plot.add_curve("sin(x)", label="sine")
    plot.set_xticks([-1, 0, 1], ["A", "B", "C"])
    plot.set_yticks([-1, 1], ["lo", "hi"])

    view = QQuickView()
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    view.rootObject().setProperty("plot", plot)
    view.show()
    assert QTest.qWaitForWindowExposed(view)
    app.processEvents()
    root = view.rootObject()
    furniture = root.findChild(QQuickItem, "plotFurniture")
    x_repeater = root.findChild(QQuickItem, "tickRepeaterX")
    y_repeater = root.findChild(QQuickItem, "tickRepeaterY")
    legend_frame = root.findChild(QQuickItem, "legendFrame")
    assert furniture is not None and x_repeater is not None and y_repeater is not None
    assert furniture.property("effectiveTicksX") == [[-1.0, "A"], [0.0, "B"], [1.0, "C"]]
    assert x_repeater.property("count") == 3
    assert y_repeater.property("count") == 2
    assert legend_frame is not None and legend_frame.property("visible") is True
    view.hide()


def test_grid_ticks_axes_and_title_are_drawn(app: QGuiApplication) -> None:
    """The QML furniture: grid lines exactly at the tick positions, the axes through world
    (0,0) with the tick labels riding them, and the title.

    The positions come from the camera, so the test checks the mapping the view does rather
    than repeating a hard-coded one.
    """
    image, plot = _render_plot(app, "sin(x)", furniture=True, title="QMLMathPlot")
    camera = plot.camera
    dpr = image.devicePixelRatio()

    def screen(world_x: float, world_y: float = 0.0) -> tuple[int, int]:
        return (int(round(((world_x - camera.centre.x()) / camera.scale_x + WIDTH / 2) * dpr)),
                int(round(((camera.centre.y() - world_y) / camera.scale_y + HEIGHT / 2) * dpr)))

    # a grid line runs through the tick at world x = 4 (its column is lit end to end) and there
    # is none at x = 5, where only the curve and the axes cross the column
    grid_x, _ = screen(4.0)
    assert _column_ratio(image, grid_x) > 0.9, "no grid line at the tick x=4"
    between_x, _ = screen(5.0)
    assert _column_ratio(image, between_x) < 0.2, "there should be no grid line between the ticks"
    horizontal_y = screen(0.0, 1.0)[1]
    lit = sum(_lit(image, x, horizontal_y) for x in range(0, image.width()))
    assert lit > 0.9 * image.width(), "no grid line at the tick y=1"

    # the axes are the lines through the origin: dark pixels on the centre column and the
    # middle row (the curve passes through the origin too, so check away from it)
    origin_x, origin_y = screen(0.0, 0.0)
    assert _dark(image, origin_x, screen(0.0, 1.5)[1]), "no y-axis through the centre column"
    assert _dark(image, screen(3.5, 0.0)[0], origin_y), "no x-axis through the middle row"
    # and the background is white away from every line
    assert not _lit(image, *screen(3.0, 1.2)), "background should be white away from the curves and lines"

    # tick marks and labels sit on the axes: the label of the x tick at 2 is right below the
    # x-axis (between the axis and the bottom edge), not at the bottom edge itself
    axis_label_y = origin_y + int(6 * dpr)
    near_axis = sum(_lit(image, x, axis_label_y) for x in range(*sorted((screen(2.0)[0] - 12, screen(2.0)[0] + 12))))
    assert near_axis > 0, "the x tick labels are not riding the x axis"

    # the title sits at the top centre
    strip = int(16 * dpr)
    top = sum(_lit(image, x, y) for x in range(image.width() // 3, 2 * image.width() // 3)
              for y in range(0, strip))
    assert top > 0, "the title is not drawn at the top"


def test_axis_thickness_is_stable_across_positions(app: QGuiApplication) -> None:
    """The axis must not alternate between one and two physical pixels while zooming.

    It is a plain QML ``Rectangle`` in logical coordinates. At DPR 1.5 its 1.07-logical-px
    width is 1.6 device px; if its edges are not snapped to the device grid the rasteriser
    covers one or two rows depending on the fractional world position. The view snaps the
    line's centre so its edges are integral device pixels and uses an integral device width.
    """
    counts: set[int] = set()
    expected = 0
    for pan in (0.0, 0.3, 0.7, 1.1, -1.7, 2.3):
        image, plot = _render_plot(app, "sin(x)", pan_pixels=pan, furniture=True)
        dpr = image.devicePixelRatio()
        expected = max(1, round(plot.axis_width * dpr))
        camera = plot.camera
        # a column away from the curve's and labels' crossings of the x axis
        x = image.width() // 8
        y0 = (camera.centre.y() / camera.scale_y + HEIGHT / 2) * dpr
        rows = [y for y in range(int(round(y0)) - 3, int(round(y0)) + 4)
                if _dark(image, x, y)]
        counts.add(len(rows))
    assert counts == {expected}, \
        f"axis thickness varied across pans: {sorted(counts)} rows, expected {expected} device px"


def test_axis_pins_to_the_edge_when_the_origin_is_off_screen(app: QGuiApplication) -> None:
    """Panning the origin out of the view must not lose the labels: the axis sticks to the
    nearest edge (the Desmos behaviour) instead of leaving the item."""
    image, plot = _render_plot(app, "sin(x)", furniture=True, pan_pixels=-9000.0)
    height, width = image.height(), image.width()
    assert plot.camera.ylim.y() < 0.0, "the origin should be out of the view above"

    # the x-axis is now the top edge of the item, and it is still a full-width line
    row = sum(_lit(image, x, 0) for x in range(width))
    assert row > 0.9 * width, "the pinned x axis is not drawn along the edge"
    # the y axis is unchanged (x = 0 is still visible) and the labels are on screen
    centre_x = width // 2
    assert _dark(image, centre_x, height // 3), "the y axis at x=0 disappeared"
