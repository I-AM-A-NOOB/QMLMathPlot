"""Export layer: render a region of this canvas at a pixel size (docs/api-design.md §9).

An export is not a re-draw at another ``dpi`` — the plot *is* a Qt scene, so the same scene is
rendered into a hidden ``QQuickWidget`` and read back synchronously. Everything (curves, grid,
axes, theme) follows the live styling, so an export cannot drift from what the user sees; only
the camera is *borrowed* for the duration of the render and restored before the event loop runs
again, so the live view never repaints the export state.

QtWidgets is imported lazily (a pure-QML app that never exports never loads it).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QColor, QImage

if TYPE_CHECKING:  # pragma: no cover - typing only; the real imports stay lazy
    from PySide6.QtQuickWidgets import QQuickWidget
    from PySide6.QtWidgets import QApplication

    from .plot import Plot

__all__ = ["ADJUSTABLES", "render"]

#: How a requested range and a requested pixel size are reconciled (see the module docstring).
ADJUSTABLES = ("box", "datalim", "stretch")


def _pair(value: Sequence[float], name: str) -> tuple[float, float]:
    try:
        lo, hi = (float(value[0]), float(value[1]))
    except (TypeError, IndexError, KeyError):
        raise ValueError(f"{name} must be a (min, max) pair, got {value!r}") from None
    if not hi > lo:
        raise ValueError(f"{name} must be increasing, got {value!r}")
    return lo, hi


def _widgets() -> tuple[type[QApplication], type[QQuickWidget]]:
    """The QtWidgets classes the export needs, or a clear error when they are not usable."""
    try:
        from PySide6.QtQuickWidgets import QQuickWidget
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # pragma: no cover - both ship with PySide6
        raise RuntimeError("exporting needs QtWidgets/QtQuickWidgets (PySide6)") from exc
    if not isinstance(QApplication.instance(), QApplication):
        # Creating a QWidget under a bare QGuiApplication aborts the process inside Qt (and
        # `QApplication.instance()` *is* the QGuiApplication there), so this has to be refused
        # before the offscreen widget is touched.
        raise RuntimeError(
            "exporting needs a QApplication: the offscreen render uses a QQuickWidget; "
            "create one (MathPlotWidget does it for you) or use a widgets host"
        )
    return QApplication, QQuickWidget


def render(
    plot: Plot,
    *,
    xlim: Sequence[float] | None = None,
    ylim: Sequence[float] | None = None,
    width: float | None = None,
    height: float | None = None,
    dpi: float = 1.0,
    adjustable: str | None = None,
    transparent: bool = False,
) -> QImage:
    """Render ``plot`` offscreen and return the grabbed image, in device pixels.

    ``xlim`` / ``ylim`` default to the camera (the live centre and scale, extended to the export
    size), ``width`` / ``height`` default to the live view's device size (``dpi`` multiplies
    both), and ``adjustable`` reconciles the two parameter sets.
    """
    dpi = float(dpi)
    if not dpi > 0:
        raise ValueError(f"dpi must be positive, got {dpi!r}")
    mode = "box" if adjustable is None else str(adjustable)      # matplotlib's own default
    if mode not in ADJUSTABLES:
        raise ValueError(f"adjustable must be one of {ADJUSTABLES}, got {adjustable!r}")
    for name, value in (("width", width), ("height", height)):
        if value is not None and not float(value) > 0:
            raise ValueError(f"{name} must be positive, got {value!r}")

    _app, quick_widget = _widgets()
    from .view import qml_component_path, register_qml_types

    register_qml_types()
    camera = plot.camera
    live = (camera.centre.x(), camera.centre.y(), camera.scale_x, camera.scale_y)
    live_size = (camera.viewport.x(), camera.viewport.y())
    background = plot.background

    widget: QQuickWidget = quick_widget()
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.setResizeMode(quick_widget.ResizeMode.SizeRootObjectToView)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    image = QImage()
    device = (0, 0)
    try:
        if transparent:
            plot.background = QColor(0, 0, 0, 0)
            widget.setClearColor(QColor(0, 0, 0, 0))
        widget.setSource(QUrl.fromLocalFile(qml_component_path()))
        if widget.status() != quick_widget.Status.Ready:
            errors = "; ".join(error.toString() for error in widget.errors())
            raise RuntimeError(f"the QML component failed to load: {errors}")
        root = widget.rootObject()          # the widget itself has no such property
        if root is None:
            raise RuntimeError("the QML component has no root object")
        root.setProperty("plot", plot)

        # width/height are device pixels, so the offscreen view gets the logical size
        pixel_ratio = widget.devicePixelRatioF()
        device = (
            round((float(width) if width is not None else live_size[0] * pixel_ratio) * dpi),
            round((float(height) if height is not None else live_size[1] * pixel_ratio) * dpi),
        )
        logical = (device[0] / pixel_ratio, device[1] / pixel_ratio)
        widget.resize(round(logical[0]), round(logical[1]))
        widget.show()                       # offscreen: WA_DontShowOnScreen, never exposed

        # The range: the camera, extended to the export size, unless the caller asked for one
        range_x = _pair(xlim, "xlim") if xlim is not None else (
            live[0] - 0.5 * logical[0] * live[2], live[0] + 0.5 * logical[0] * live[2])
        range_y = _pair(ylim, "ylim") if ylim is not None else (
            live[1] - 0.5 * logical[1] * live[3], live[1] + 0.5 * logical[1] * live[3])
        span_x, span_y = range_x[1] - range_x[0], range_y[1] - range_y[0]
        centre = (0.5 * (range_x[0] + range_x[1]), 0.5 * (range_y[0] + range_y[1]))

        if mode == "stretch":               # the range maps onto the size exactly
            scale = (span_x / logical[0], span_y / logical[1])
        elif mode == "box":                 # the live ratio, the range drawn as large as it fits
            # The largest scale (the smallest world units per pixel) for which the requested
            # range still fits: the binding axis is exact, the other keeps background margins.
            # Drawn without distortion — a world circle stays round, only the visible span
            # changes when the requested range's aspect differs from the size's.
            ratio = live[2] / live[3]
            scale_x = max(span_x / logical[0], ratio * span_y / logical[1])
            scale = (scale_x, scale_x / ratio)
        else:                               # "datalim": the live scales, the limits expand
            scale = (max(live[2], span_x / logical[0]), max(live[3], span_y / logical[1]))

        camera.setViewport(logical[0], logical[1])
        camera.set_view(centre[0], centre[1], scale[0], scale[1])
        image = widget.grabFramebuffer()
    finally:
        # Restore before the event loop runs again, so no live view repaints the export state.
        camera.set_view(live[0], live[1], live[2], live[3])
        camera.setViewport(live_size[0], live_size[1])
        plot.background = background
        widget.hide()
        widget.deleteLater()

    if image.size() != QSize(device[0], device[1]):
        # At a fractional devicePixelRatio the offscreen widget can land within one pixel of the
        # requested size; make the promise exact (a 1 px nearest resample is invisible).
        image = image.scaled(
            device[0], device[1],
            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation,
        )
    return image
