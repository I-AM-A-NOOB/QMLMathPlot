"""End-to-end smoke tests: really open a window and render, then check pixels for the two
pieces of the puzzle (screen-space stroke + undersampled envelope band) and for the QML
furniture (grid, ticks, title).

Grabbing the image must use the asynchronous ``QQuickItem.grabToImage()``: the synchronous
handshake of ``QQuickWindow.grabWindow()`` deadlocks with the Python-side scene-graph
objects (the window shows up as "not responding").

The backend is chosen by the ``QSG_RHI_BACKEND`` environment variable (unset = Qt default).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from qmlmathplot import Plot, qml_component_path

pytestmark = pytest.mark.gui

WIDTH, HEIGHT = 900, 600
BACKGROUND = (0x14, 0x14, 0x1E)  # background of PlotView.qml
HOME = (-6.0, 6.0, -2.0, 2.0)    # the classic view: the pixel positions below assume it


def _render_plot(app: QGuiApplication, expression: str | None = None,
                 pan_pixels: float = 0.0, zoom_steps: int = 0, furniture: bool = False,
                 title: str = ""):
    """Open the component at the classic 900x600 view and grab one frame: (image, plot)."""
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(WIDTH, HEIGHT)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]

    root = view.rootObject()
    if expression is None:
        # No injection: the component brings its own plot and its default sin(x) curve. Its
        # own plot is deliberately not read back (a QML-declared property of a registered
        # Python type has no Python converter), so the component's own home view stands.
        plot = None
    else:
        # MVVM: the app side creates the model and injects it into the component
        plot = Plot()
        plot.add_curve(expression)
        root.setProperty("plot", plot)
    # The pixel expectations below measure the *curve*, and the furniture sits exactly on the
    # columns being measured (the grid at x=0, the tick labels along the bottom edge), so it
    # stays off except in the test that is about the furniture itself.
    if plot is not None and not furniture:
        plot.grid = False
        plot.ticks_visible = False
    if plot is not None:
        plot.title = title

    view.show()
    if not QTest.qWaitForWindowExposed(view):
        pytest.skip("no usable display/GPU scenegraph")

    if plot is not None:
        camera = plot.camera
        camera.setViewport(float(WIDTH), float(HEIGHT))
        camera.xlim = (HOME[0], HOME[1])
        camera.ylim = (HOME[2], HOME[3])
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
    """Without an injected model the component brings its own plot and curve (the standalone
    usability promised by the README)."""
    image = _render(app)
    assert _curve_count(image) > 100, "the component's own default sin(x) curve should draw"


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
    assert outside < 50, f"sin(1/x) has range ±1, so there should be no solid pixels outside it, got {outside}"
    width = _solid_band_columns(image) / 1.5  # the grab carries devicePixelRatio
    assert 5 < width < 60, f"the solid band width should be a single-digit to a few dozen logical columns, got {width:.0f}"


def test_smooth_column_stays_thin(app: QGuiApplication) -> None:
    ratio = _center_column_ratio(_render(app, "sin(x)"))
    assert ratio < 0.05, f"sin(x) should still be a thin line at x=0, got {ratio:.2f}"


def _column_lit(image: QImage, world_x: float) -> int:
    """Number of lit pixels in the column at the given world x."""
    x = min(image.width() - 1, max(0, int((world_x + 6) / 12 * image.width())))
    return sum(_lit(image, x, y) for y in range(image.height()))


def test_asymptote_is_not_connected(app: QGuiApplication) -> None:
    """1/x and tan(x) must not draw a vertical connecting line at their poles
    (a jump over a ±inf range)."""
    for expr, poles in (("1/x", [0.0]), ("tan(x)", [1.5708])):
        image = _render(app, expr)
        for pole in poles:
            lit = _column_lit(image, pole)
            assert lit < 30, f"{expr} should have no connecting line at x={pole}, got {lit} lit pixels"


def test_out_of_domain_is_blank(app: QGuiApplication) -> None:
    """log(x), sqrt(x) and asin(x) draw nothing outside their domain (x<0 / |x|>1)."""
    for expr in ("log(x)", "sqrt(x)"):
        image = _render(app, expr)
        left = sum(_lit(image, x, y) for x in range(0, image.width() // 2 - 4)
                   for y in range(0, image.height(), 5))
        assert left == 0, f"{expr} should have no pixels for x<0, got {left}"
    image = _render(app, "asin(x)")
    # left end x in [-6, -1]: everything with |x|>1 lies outside the domain
    far = sum(_lit(image, x, y) for x in range(0, int((-1.0 + 6) / 12 * image.width()))
              for y in range(0, image.height(), 5))
    assert far == 0, f"asin(x) should have no pixels for |x|>1, got {far}"


def test_log_descent_is_not_cut(app: QGuiApplication) -> None:
    """log(x) must be drawn all the way out of the viewport as x→0+, not cut off by the
    lower bound of the sampling envelope ("gradually thinning away").

    The viewport is panned down by 6 units (y in [-8,-4]): the curve's column at x≈0.004
    sweeps from -4.5 to -inf within that single column, so lit pixels must land near the
    bottom edge of the viewport.
    """
    image = _render(app, "log(x)", pan_pixels=-900.0)
    height = image.height()
    column = _column_lit(image, 0.004)
    assert column > 0, "log(x) should have pixels at x≈0.004"
    # the descending part is right by the centre column (at x≈0.004 log is already below -7,
    # so the whole column should have pixels)
    center = image.width() // 2
    bottom = sum(_lit(image, x, y) for x in range(center - 4, center + 5)
                 for y in range(int(height * 0.97), height))
    assert bottom > 0, "log(x)'s descending part should be drawn all the way to the viewport's bottom edge"


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


def test_grid_ticks_and_title_are_drawn(app: QGuiApplication) -> None:
    """The QML furniture: grid lines exactly at the tick positions, tick marks along the
    bottom edge and the title at the top.

    The positions are derived from the camera, so the test checks the mapping the view does
    rather than repeating a hard-coded one.
    """
    image, plot = _render_plot(app, "sin(x)", furniture=True, title="QMLMathPlot")
    camera = plot.camera
    dpr = image.devicePixelRatio()

    def screen(world_x: float, world_y: float = 0.0) -> tuple[int, int]:
        return (int(round(((world_x - camera.centre.x()) / camera.scale_x + WIDTH / 2) * dpr)),
                int(round(((camera.centre.y() - world_y) / camera.scale_y + HEIGHT / 2) * dpr)))

    # a grid line runs through the tick at world x = 4 (its column is lit end to end) and
    # there is none at x = 5, where only the curve crosses the column
    grid_x, _ = screen(4.0)
    assert _column_ratio(image, grid_x) > 0.9, "no grid line at the tick x=4"
    between_x, _ = screen(5.0)
    assert _column_ratio(image, between_x) < 0.2, "there should be no grid line between the ticks"
    horizontal_y = screen(0.0, 1.0)[1]
    lit_rows = sum(_lit(image, x, horizontal_y) for x in range(0, image.width()))
    assert lit_rows > 0.9 * image.width(), "no grid line at the tick y=1"

    # tick marks and labels hug the bottom edge; the title sits at the top centre
    strip = int(16 * dpr)
    bottom = sum(_lit(image, x, y, threshold=15)
                 for x in range(0, image.width(), 3) for y in range(image.height() - strip, image.height()))
    assert bottom > 0, "no tick marks or labels along the bottom edge"
    top = sum(_lit(image, x, y, threshold=15)
              for x in range(image.width() // 3, 2 * image.width() // 3)
              for y in range(0, strip))
    assert top > 0, "the title is not drawn at the top"
