"""Export tests: a region of the canvas rendered offscreen at a requested pixel size.

The assertions are pixel-level (docs/api-design.md §9): the image size is in device pixels, the
three ``adjustable`` modes reconcile the requested range with the requested size in the
documented way, and the live camera survives the export untouched.

The plot is set up without a widget (the camera is the state), so the expected mapping is
arithmetic rather than a second implementation of the view: every expectation below is
``device pixel = logical pixel * devicePixelRatio``.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication

from qmlmathplot import MathPlotWidget, Plot

pytestmark = pytest.mark.gui

LOGICAL = (800.0, 600.0)          # the live view the tests export from
#: Live scales of the test plots: world units per logical pixel.
SCALE = 0.01
BACKGROUND = (0xFF, 0xFF, 0xFF)   # white: matplotlib's default background


def _ratio() -> float:
    """Device pixels per logical pixel the offscreen view renders at (the screen's)."""
    screen = QGuiApplication.primaryScreen()
    assert screen is not None
    return screen.devicePixelRatio()


def _plot(expression: str, *, scale_x: float = SCALE, scale_y: float | None = None) -> Plot:
    """A plot with the furniture off and the camera set explicitly (an export needs nothing else)."""
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.line_width = 2.0
    plot.add_curve(expression)
    plot.camera.setViewport(LOGICAL[0], LOGICAL[1])
    plot.camera.set_view(0.0, 0.0, scale_x, scale_y if scale_y is not None else scale_x)
    return plot


def _square_plot(expression: str, scale: float = SCALE) -> Plot:
    """Square units, so a world 45-degree line is a pixel 45-degree line."""
    plot = _plot(expression, scale_x=scale, scale_y=scale)
    plot.camera.aspect = 1.0
    plot.camera.set_view(0.0, 0.0, scale, scale)
    return plot


def _hue(image: QImage, name: str) -> list[tuple[int, int]]:
    """Pixels of one curve colour (the tests draw two curves and tell them apart by hue)."""
    out = []
    for x in range(image.width()):
        for y in range(image.height()):
            color = image.pixelColor(x, y)
            if name == "blue" and color.blue() - color.red() > 60:
                out.append((x, y))
            elif name == "orange" and color.red() - color.blue() > 60:
                out.append((x, y))
    return out


def _visible_spans(image: QImage) -> tuple[float, float]:
    """The world spans the export actually shows, from the drawn curves.

    The orange curve is a horizontal line at y = 0.5 (the y ruler: its row gives the y scale);
    the blue one is `x`, whose visible pixel width is the *y* range worth of x (the diagonal is
    limited by the y range), which gives the x scale. Valid whenever the visible x span is the
    wider one, which is asserted by the callers.
    """
    orange, blue = _hue(image, "orange"), _hue(image, "blue")
    assert orange and blue, "both curves must be drawn"
    centre = (image.height() - 1) / 2
    row = sum(y for _x, y in orange) / len(orange)
    per_unit_y = (centre - row) / 0.5                       # device px per world y unit
    visible_y = image.height() / per_unit_y
    columns = [x for x, _y in blue]
    per_unit_x = (max(columns) - min(columns)) / visible_y  # device px per world x unit
    visible_x = image.width() / per_unit_x
    return visible_x, visible_y


def _square_units_camera(plot: Plot, aspect: float) -> None:
    """A camera whose live unit ratio is `aspect = y_scale / x_scale` in pixels."""
    plot.camera.setViewport(LOGICAL[0], LOGICAL[1])
    plot.camera.aspect = aspect
    plot.camera.set_view(0.0, 0.0, 0.015, 0.015 / aspect)


def _lit(image: QImage, x: int, y: int, threshold: int = 60) -> bool:
    color = image.pixelColor(x, y)
    return sum(abs(channel - background) for channel, background in
               zip((color.red(), color.green(), color.blue()), BACKGROUND)) > threshold


def _lit_rows(image: QImage) -> list[int]:
    """The rows holding something (the tests export one curve and no furniture)."""
    return [y for y in range(image.height()) if any(_lit(image, x, y) for x in range(image.width()))]


def _curve_row(image: QImage) -> float:
    """Mean row of the drawn horizontal line."""
    rows = _lit_rows(image)
    assert rows, "nothing is drawn"
    return sum(rows) / len(rows)


def _bbox(image: QImage) -> tuple[int, int, int, int]:
    columns = [x for x in range(image.width()) if any(_lit(image, x, y) for y in range(image.height()))]
    rows = _lit_rows(image)
    return columns[0], columns[-1], rows[0], rows[-1]


def _slope_of(image: QImage, hue: str = "blue") -> float:
    """|dy/dx| of one curve's pixels (image rows grow downwards)."""
    columns: dict[int, list[int]] = {}
    for x, y in _hue(image, hue):
        columns.setdefault(x, []).append(y)
    assert len(columns) > image.width() // 10, "the curve is not drawn across the image"
    xs = [float(x) for x in sorted(columns)]
    ys = [sum(columns[x]) / len(columns[x]) for x in sorted(columns)]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return abs(num / sum((x - mean_x) ** 2 for x in xs))


def _slope(image: QImage) -> float:
    """|dy/dx| of the drawn curve in pixels (image rows grow downwards)."""
    xs: list[float] = []
    ys: list[float] = []
    for x in range(image.width()):
        rows = [y for y in range(image.height()) if _lit(image, x, y)]
        if rows:
            xs.append(float(x))
            ys.append(sum(rows) / len(rows))
    assert len(xs) > image.width() // 10, "the curve is not drawn across the image"
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return abs(num / sum((x - mean_x) ** 2 for x in xs))


def test_the_image_has_exactly_the_requested_device_size(app: QApplication) -> None:
    plot = _plot("sin(x)")
    for width, height in ((600, 400), (300, 200), (401, 199), (97, 53)):
        image = plot.to_image(width=width, height=height)
        assert image.size() == QSize(width, height), f"{width}x{height} came out {image.size()}"


def test_dpi_multiplies_the_device_size(app: QApplication) -> None:
    plot = _plot("sin(x)")
    assert plot.to_image(width=300, height=200, dpi=2.0).size() == QSize(600, 400)
    assert plot.to_image(width=300, height=200, dpi=0.5).size() == QSize(150, 100)


def test_no_arguments_reproduces_the_live_view(app: QApplication) -> None:
    """The defaults are the live view: its own size, its centre, its scales."""
    plot = _plot("0.5 + 0*x")                                 # a horizontal line at y = 0.5
    image = plot.to_image()
    ratio = _ratio()
    assert image.width() == pytest.approx(LOGICAL[0] * ratio, abs=2)
    assert image.height() == pytest.approx(LOGICAL[1] * ratio, abs=2)
    # the centre is the middle of the image; y = 0.5 sits 0.5/SCALE logical px above it
    expected = (LOGICAL[1] / 2 - 0.5 / SCALE) * ratio
    assert _curve_row(image) == pytest.approx(expected, abs=3)


def test_box_keeps_the_live_ratio_and_pads(app: QApplication) -> None:
    """``"box"`` (the default) is the mode that never distorts: one scale, the live ratio,
    contained so the requested range is fully visible and the leftovers stay background."""
    image = _square_plot("x").to_image(xlim=(-1, 1), ylim=(-1, 1), width=600, height=200)
    ratio = _ratio()
    # the range is 1:1 and the frame 3:1, so it fits in height: scale = 2 / (200 / ratio)
    scale = 2.0 / (200.0 / ratio)
    assert _slope(image) == pytest.approx(1.0, abs=0.1), "a world 45-degree line must stay at 45"
    left, right, top, bottom = _bbox(image)
    assert bottom - top > 0.9 * image.height(), "the fitted axis is filled"
    assert right - left < 0.5 * image.width(), "the other axis must leave background margins"
    # only the requested range is drawn: 2 world units at the fitted scale, in device pixels
    assert right - left == pytest.approx(2.0 / (scale / ratio), rel=0.1)


def test_datalim_uses_the_live_scale(app: QApplication) -> None:
    """``"datalim"`` keeps the camera's scale and expands the limits, so the requested range is
    contained while the frame shows what the live scale covers."""
    plot = _plot("0.5 + 0*x", scale_x=0.02)                   # zoomed in: 0.02 per logical px
    image = plot.to_image(xlim=(-1, 1), ylim=(-1, 1), width=600, height=400,
                          adjustable="datalim")
    ratio = _ratio()
    logical_height = image.height() / ratio
    # y = 0.5 is 0.5/0.02 = 25 logical px above the centre (with "box" it would be 66.7)
    assert _curve_row(image) == pytest.approx((logical_height / 2 - 25.0) * ratio, abs=4)
    # the requested range [-1, 1] is strictly inside the frame: it was not cropped
    for world in (-1.0, 1.0):
        row = (logical_height / 2 - world / 0.02) * ratio
        assert 0 < row < image.height() - 1, f"y={world} fell outside the export"


def test_box_draws_the_range_as_large_as_the_live_ratio_allows(app: QApplication) -> None:
    """matplotlib's ``"box"``: the requested limits are drawn as *large* as possible while
    staying fully visible, with the live unit ratio preserved.

    The camera here has the x unit twice the y unit in pixels (``aspect = 0.5``, i.e. the
    ratio ``r = scale_x_px / scale_y_px = 2`` of the report): the height asks for
    ``1 / height/dy = 100`` px per y unit, so the x unit gets 200 px per unit — the requested
    x range (±1) covers 400 of the 600 px, and the *visible* x span is 3 world units.
    """
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.line_width = 2.0
    _square_units_camera(plot, 0.5)
    plot.add_curve("x", color="#1f77b4")             # 45 degrees in world units
    plot.add_curve("0.5 + 0*x", color="#ff7f0e")     # the y ruler

    image = plot.to_image(xlim=(-1, 1), ylim=(-1, 1), width=600, height=200)
    visible_x, visible_y = _visible_spans(image)
    assert visible_y == pytest.approx(2.0, rel=0.05), "the requested y range is the binding one"
    assert visible_x == pytest.approx(3.0, rel=0.08), f"visible x span is {visible_x:.2f}, not 3"
    # no distortion: the pixels keep the live unit ratio, so a world 45-degree line is drawn at
    # px_per_y / px_per_x (0.5 here — that *is* the ratio r = 2, not a stretch). With square
    # units (aspect = 1) the same assertion is 1.0, see
    # test_box_keeps_the_live_ratio_and_pads.
    assert _slope_of(image, "blue") == pytest.approx(0.5, abs=0.05), \
        "the live unit ratio must be preserved"

    # the requested range is inside the image (fully visible, not cropped)
    ratio = _ratio()
    px_dev = image.width() / visible_x
    py_dev = image.height() / visible_y
    for world_x, world_y in ((1.0, 1.0), (-1.0, -1.0)):
        x = (image.width() - 1) / 2 + world_x * px_dev
        y = (image.height() - 1) / 2 - world_y * py_dev
        # 2 px of slack: the requested range fills the binding axis exactly, so its edge lands
        # on the outermost pixel row/column
        assert -2 <= x <= image.width() + 1 and -2 <= y <= image.height() + 1, (world_x, world_y)
    assert px_dev / py_dev == pytest.approx(2.0, rel=0.05), "the live unit ratio (r = 2)"
    assert isinstance(ratio, float)


def test_box_span_is_forced_when_the_other_axis_binds(app: QApplication) -> None:
    """The mirror case, locked so the difference is explicit: with the live ratio the other way
    round (``aspect = 2``, the y unit twice the x unit in pixels — the default "auto" ratio at
    the reference size), the same request must show x from -6 to 6.

    The requested y range (±1) fills the image height, and preserving the live ratio then fixes
    the x span at 12: asking for 3 would mean cropping the y range to ±0.25, and no mode crops.
    """
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    plot.line_width = 2.0
    plot.camera.setViewport(LOGICAL[0], LOGICAL[1])
    plot.camera.aspect = 2.0
    plot.camera.set_view(0.0, 0.0, 0.015, 0.0075)
    plot.add_curve("x", color="#1f77b4")
    plot.add_curve("0.5 + 0*x", color="#ff7f0e")

    image = plot.to_image(xlim=(-1, 1), ylim=(-1, 1), width=600, height=200)
    visible_x, visible_y = _visible_spans(image)
    assert visible_y == pytest.approx(2.0, rel=0.05), "the requested y range is exactly the image"
    assert visible_x == pytest.approx(12.0, rel=0.08), "the ratio forces the x span"
    assert _slope_of(image, "blue") == pytest.approx(2.0, abs=0.1), "still no distortion"


def test_stretch_fills_the_frame_and_distorts(app: QApplication) -> None:
    """``"stretch"`` maps the range onto the size exactly; a mismatched aspect distorts (the
    documented trade-off of the report-figure mode)."""
    image = _square_plot("x").to_image(xlim=(-1, 1), ylim=(-1, 1), width=600, height=200,
                                       adjustable="stretch")
    assert _slope(image) == pytest.approx(1 / 3, abs=0.1), "a 1:1 range in a 3:1 frame distorts"
    left, right, top, bottom = _bbox(image)
    assert left < 0.05 * image.width() and right > 0.95 * image.width()
    assert top < 0.05 * image.height() and bottom > 0.95 * image.height()


def test_the_modes_agree_without_an_explicit_range(app: QApplication) -> None:
    """With no range the derived one already has the export size's aspect, so every mode lands
    on the same drawing (the reason the default is harmless for the common case)."""
    images = [_plot("x").to_image(width=400, height=300, adjustable=mode)
              for mode in (None, "box", "datalim", "stretch")]
    assert all(image.size() == images[0].size() for image in images)
    for image in images[1:]:
        assert _slope(image) == pytest.approx(_slope(images[0]), rel=0.05)
        assert _bbox(image) == _bbox(images[0])


def test_parameters_are_validated(app: QApplication) -> None:
    plot = _plot("sin(x)")
    with pytest.raises(ValueError):
        plot.to_image(adjustable="crop")
    with pytest.raises(ValueError):
        plot.to_image(dpi=0.0)
    with pytest.raises(ValueError):
        plot.to_image(width=-10)
    with pytest.raises(ValueError):
        plot.to_image(height=0)
    with pytest.raises(ValueError):
        plot.to_image(xlim=(1, -1))
    with pytest.raises(ValueError):
        plot.to_image(ylim=(0.0, 0.0))


def test_transparent_clears_to_alpha_zero(app: QApplication) -> None:
    plot = _plot("sin(x)")
    assert plot.to_image(width=200, height=150).pixelColor(2, 2).alpha() == 255
    clear = plot.to_image(width=200, height=150, transparent=True)
    assert clear.pixelColor(2, 2).alpha() == 0, "a transparent export must clear to alpha 0"
    assert plot.background.alpha() > 0, "the live background must be restored"


def test_the_live_camera_and_background_survive_the_export(app: QApplication) -> None:
    plot = _plot("sin(x)", scale_x=0.013, scale_y=0.007)
    camera = plot.camera

    def state() -> tuple[object, ...]:
        return (camera.centre.x(), camera.centre.y(), camera.zoom, camera.aspect,
                camera.scale_x, camera.scale_y, camera.viewport.x(), camera.viewport.y(),
                plot.background.name())

    before = state()
    plot.to_image(xlim=(-1, 1), ylim=(-1, 1), width=300, height=200, transparent=True,
                  adjustable="stretch")
    assert state() == before


def test_savefig_writes_the_png(app: QApplication, tmp_path) -> None:
    plot = _plot("sin(x)")
    path = tmp_path / "export.png"
    plot.savefig(str(path), xlim=(-1, 1), ylim=(-1, 1), width=320, height=200)
    assert path.exists()
    assert QImage(str(path)).size() == QSize(320, 200)
    with pytest.raises(OSError):
        plot.savefig(str(tmp_path / "no-such-dir" / "x.png"), width=100, height=100)


def test_qml_can_call_savefig_and_to_image(app: QApplication) -> None:
    """Both are exposed to QML with their defaults (a path only / no arguments)."""
    plot = _plot("sin(x)")
    meta = plot.metaObject()
    assert meta.indexOfMethod("savefig(QString)") >= 0
    assert meta.indexOfMethod("to_image()") >= 0                     # the bare call works …
    image = plot.to_image()
    ratio = _ratio()
    assert image.width() == pytest.approx(LOGICAL[0] * ratio, abs=2)
    assert _lit_rows(image), "the QML path must render the curve"


def test_a_pure_qml_host_gets_a_clear_error() -> None:
    """A QGuiApplication-only host (pure Qt Quick): the export refuses with a clear error
    instead of letting Qt abort the process — creating a QWidget there is fatal inside Qt."""
    script = (
        "import sys\n"
        "from PySide6.QtGui import QGuiApplication\n"
        "app = QGuiApplication(sys.argv[:1])\n"
        "from qmlmathplot import Plot\n"
        "plot = Plot()\n"
        "plot.add_curve('sin(x)')\n"
        "plot.to_image(width=64, height=64)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode != 0, "the export should have refused"
    assert "needs a QApplication" in result.stderr, result.stderr
    assert result.returncode > 0, "the process must not be aborted by Qt"


def test_a_widget_host_can_export_a_region_it_is_not_showing(app: QApplication) -> None:
    """The realistic host: a widget owning the plot, exporting a region for a report."""
    widget = MathPlotWidget("sin(1/x)")
    widget.resize(500, 400)
    widget.show()
    widget.plot.grid = False
    widget.plot.ticks_visible = False
    image = widget.plot.to_image(xlim=(-1, 1), ylim=(-1, 1), width=240, height=120)
    assert image.size() == QSize(240, 120)
    assert _lit_rows(image), "the widget's own curve must appear in the export"
    widget.hide()
