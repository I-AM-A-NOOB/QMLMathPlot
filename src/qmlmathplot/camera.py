"""Camera layer: where we look at the infinite canvas, plus the nice-number tick algorithm.

The camera stores three numbers that do not depend on the widget at all — ``centre``,
``zoom`` (world units per *logical* pixel on x) and ``aspect`` (the ratio of the y scale to
the x scale; ``"auto"`` leaves it to the widget's shape). The visible range (``xlim`` /
``ylim``) is *derived* from them and the last reported size, so resizing changes what is
visible without touching the state — which is what makes an infinite canvas cheap.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QVector2D

__all__ = ["HOME_SIZE", "HOME_VIEW", "Camera", "nice_ticks"]

#: Home view — the classic 12x4 window — as (xmin, xmax, ymin, ymax).
#:
#: The home is a *view*, but the camera stores scales, so the two meet at a reference size:
#: the first reported size turns the home view into (zoom, y scale) for that widget, and
#: the constants below are only the fallback used before any size is known.
HOME_VIEW = (-6.0, 6.0, -2.0, 2.0)
#: Reference size for the home view (before the first ``setViewport``).
HOME_SIZE = (900.0, 600.0)


def _pair(value: object) -> tuple[float, float]:
    """Two floats from a QVector2D, a QPointF or any 2-sequence."""
    if isinstance(value, (QVector2D,)):
        return (value.x(), value.y())
    x, y = value  # type: ignore[misc]  # documented: any pair-like value
    return (float(x), float(y))


def nice_ticks(lo: float, hi: float, target: int = 8) -> list[tuple[float, str]]:
    """Ticks covering ``[lo, hi]`` at nice-number steps (1/2/5 x 10^n), about ``target`` of
    them, as ``(value, label)``.

    Pure and Qt-free: the tick values are decided once per camera change (not per frame) and
    QML only positions the labels it is given.
    """
    if not hi > lo or target < 1:
        return []
    raw = (hi - lo) / target
    magnitude = 10.0 ** math.floor(math.log10(raw))
    for multiple in (1.0, 2.0, 5.0, 10.0):
        if raw <= multiple * magnitude:
            step = multiple * magnitude
            break
    else:  # pragma: no cover - the 10.0 branch always matches
        step = 10.0 * magnitude

    decimals = max(0, -math.floor(math.log10(step) + 1e-9))
    first = math.ceil(lo / step - 1e-9) * step
    out: list[tuple[float, str]] = []
    for i in range(int(math.floor((hi - first) / step + 1e-9)) + 1):
        # Round to the step's own precision: repeated addition accumulates noise (0.6000000000000001)
        # and the label would then disagree with the position.
        value = round(first + i * step, decimals)
        if abs(value) < step * 1e-6:
            value = 0.0                       # no "-0" labels
        out.append((value, _tick_label(value, step, decimals)))
    return out


def _tick_label(value: float, step: float, decimals: int) -> str:
    if abs(value) >= 1e7 or (value != 0.0 and abs(step) < 1e-7):
        return f"{value:g}"
    return f"{value:.{decimals}f}"


def _qml_pairs(ticks: list[tuple[float, str]]) -> list[list[float | str]]:
    """Ticks as two-element *lists* for QML: a Python tuple crosses a QVariant as an opaque
    value (QML cannot index it), so the pairs have to be lists to be readable there."""
    return [[value, label] for value, label in ticks]


class Camera(QObject):
    """The view onto the infinite canvas: centre, zoom (units per logical pixel) and aspect.

    ``aspect`` is the ratio of the y scale to the x scale, so ``1.0`` means square units (a
    world circle is drawn round) and ``"auto"`` keeps the two scales independent, letting the
    widget's shape decide how much canvas is visible.
    """

    viewChanged = Signal()
    aspectChanged = Signal()
    ticksChanged = Signal()
    zoomStepChanged = Signal()
    panEnabledChanged = Signal()
    zoomEnabledChanged = Signal()

    #: Scale limits (world units per pixel), to keep panning/zooming reversible.
    MIN_SCALE = 1e-12
    MAX_SCALE = 1e12

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._centre = QVector2D(0.0, 0.0)
        self._zoom = (HOME_VIEW[1] - HOME_VIEW[0]) / HOME_SIZE[0]
        self._aspect: str | float = "auto"
        self._y_scale = (HOME_VIEW[3] - HOME_VIEW[2]) / HOME_SIZE[1]
        self._size: tuple[float, float] | None = None
        # True while the home *view* has not been resolved for a real size: the first report
        # then sets the scales from it, so a window opened at any size shows the home window.
        self._home = True
        self._zoom_step = 0.9
        self._pan_enabled = True
        self._zoom_enabled = True

    # ------------------------------------------------------------- state
    def _get_centre(self) -> QVector2D:
        return QVector2D(self._centre)

    def _set_centre(self, value: object) -> None:
        x, y = _pair(value)
        if (x, y) == (self._centre.x(), self._centre.y()):
            return
        self._centre = QVector2D(x, y)
        self._home = False
        self._view_changed()

    # "QVariant" rather than QVector2D so a plain ``(x, y)`` from Python is accepted too.
    centre: QVector2D = Property("QVariant", _get_centre, _set_centre, notify=viewChanged)

    def _get_zoom(self) -> float:
        return self._zoom

    def _set_zoom(self, value: float) -> None:
        self._scale_to(float(value))

    zoom: float = Property(float, _get_zoom, _set_zoom, notify=viewChanged)

    def _get_aspect(self) -> str | float:
        return self._aspect

    def _set_aspect(self, value: str | float) -> None:
        if isinstance(value, str):
            if value != "auto":
                raise ValueError(f'aspect must be "auto" or a positive number, got {value!r}')
        elif not float(value) > 0:
            raise ValueError(f"aspect must be positive, got {value!r}")
        if value == self._aspect:
            return
        old_y = self.scale_y
        self._aspect = value
        self._home = False
        if isinstance(value, str):
            self._y_scale = old_y            # switching to "auto" keeps the current shape
        else:
            # Expand, never crop: a numeric aspect may only show *more* than was visible.
            self._scale_to(max(self._zoom, float(value) * old_y), keep_ratio=False)
        self.aspectChanged.emit()
        self._view_changed()

    aspect: str | float = Property("QVariant", _get_aspect, _set_aspect, notify=aspectChanged)

    def _get_scale_x(self) -> float:
        return self._zoom

    def _get_scale_y(self) -> float:
        if isinstance(self._aspect, str):
            return self._y_scale
        return self._zoom / float(self._aspect)

    scale_x: float = Property(float, _get_scale_x, notify=viewChanged)
    scale_y: float = Property(float, _get_scale_y, notify=viewChanged)

    def _scale_to(self, value: float, *, keep_ratio: bool = True) -> None:
        """Set the x scale; in "auto" mode both scales move together (a uniform zoom)."""
        value = min(max(value, self.MIN_SCALE), self.MAX_SCALE)
        if value == self._zoom:
            return
        factor = value / self._zoom
        self._zoom = value
        if keep_ratio and isinstance(self._aspect, str):
            self._y_scale = min(max(self._y_scale * factor, self.MIN_SCALE), self.MAX_SCALE)
        self._home = False
        self._view_changed()

    # ------------------------------------------------------- derived ranges
    def _effective_size(self) -> tuple[float, float]:
        """Size used for the derived ranges: the last reported one, or the reference."""
        return self._size if self._size is not None else HOME_SIZE

    def _get_xlim(self) -> QVector2D:
        width, _ = self._effective_size()
        half = 0.5 * width * self.scale_x
        return QVector2D(self._centre.x() - half, self._centre.x() + half)

    def _set_xlim(self, value: object) -> None:
        lo, hi = _pair(value)
        width, _ = self._effective_size()
        if not hi > lo or width <= 0:
            return
        self._centre = QVector2D(0.5 * (lo + hi), self._centre.y())
        self._scale_to((hi - lo) / width)    # keeps the ratio: y follows the same factor

    def _get_ylim(self) -> QVector2D:
        _, height = self._effective_size()
        half = 0.5 * height * self.scale_y
        return QVector2D(self._centre.y() - half, self._centre.y() + half)

    def _set_ylim(self, value: object) -> None:
        lo, hi = _pair(value)
        _, height = self._effective_size()
        if not hi > lo or height <= 0:
            return
        self._centre = QVector2D(self._centre.x(), 0.5 * (lo + hi))
        if isinstance(self._aspect, str):
            self._y_scale = min(max((hi - lo) / height, self.MIN_SCALE), self.MAX_SCALE)
            self._home = False
            self._view_changed()
        else:
            self._scale_to((hi - lo) / height * float(self._aspect), keep_ratio=False)

    xlim: QVector2D = Property("QVariant", _get_xlim, _set_xlim, notify=viewChanged)
    ylim: QVector2D = Property("QVariant", _get_ylim, _set_ylim, notify=viewChanged)

    def _get_viewport(self) -> QVector2D:
        width, height = self._effective_size()
        return QVector2D(width, height)

    #: Last reported size in logical pixels (the reference size before the first report).
    viewport: QVector2D = Property("QVariant", _get_viewport, notify=viewChanged)

    # --------------------------------------------------------------- ticks
    def tick_values(self) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
        """Ticks for both axes of the visible range, as ``(value, label)`` pairs."""
        xlim, ylim = self._get_xlim(), self._get_ylim()
        return nice_ticks(xlim.x(), xlim.y()), nice_ticks(ylim.x(), ylim.y())

    def _get_ticks_x(self) -> list[list[float | str]]:
        return _qml_pairs(self.tick_values()[0])

    def _get_ticks_y(self) -> list[list[float | str]]:
        return _qml_pairs(self.tick_values()[1])

    ticks_x: list[tuple[float, str]] = Property("QVariant", _get_ticks_x, notify=ticksChanged)
    ticks_y: list[tuple[float, str]] = Property("QVariant", _get_ticks_y, notify=ticksChanged)

    # ----------------------------------------------------------- behaviour
    def _get_zoom_step(self) -> float:
        return self._zoom_step

    def _set_zoom_step(self, value: float) -> None:
        value = float(value)
        if not 0.0 < value < 1.0:
            raise ValueError(f"zoomStep must be in (0, 1), got {value!r}")
        if value == self._zoom_step:
            return
        self._zoom_step = value
        self.zoomStepChanged.emit()

    #: Scale factor of one wheel notch (0.9 = 10% closer per notch).
    zoomStep: float = Property(float, _get_zoom_step, _set_zoom_step, notify=zoomStepChanged)

    def _get_pan_enabled(self) -> bool:
        return self._pan_enabled

    def _set_pan_enabled(self, value: bool) -> None:
        value = bool(value)
        if value == self._pan_enabled:
            return
        self._pan_enabled = value
        self.panEnabledChanged.emit()

    panEnabled: bool = Property(bool, _get_pan_enabled, _set_pan_enabled, notify=panEnabledChanged)

    def _get_zoom_enabled(self) -> bool:
        return self._zoom_enabled

    def _set_zoom_enabled(self, value: bool) -> None:
        value = bool(value)
        if value == self._zoom_enabled:
            return
        self._zoom_enabled = value
        self.zoomEnabledChanged.emit()

    zoomEnabled: bool = Property(
        bool, _get_zoom_enabled, _set_zoom_enabled, notify=zoomEnabledChanged
    )

    def _view_changed(self) -> None:
        self.viewChanged.emit()
        self.ticksChanged.emit()

    @Slot(float, float, float, float)
    def set_view(self, centre_x: float, centre_y: float, scale_x: float, scale_y: float) -> None:
        """Set the centre and *both* scales at once (world units per logical pixel).

        The camera normally derives the y scale from ``aspect``; this is the primitive for a
        caller that has two scales in hand — the exporter borrowing the camera for a render,
        and restoring it afterwards. ``"auto"`` stays "auto" (the scales are simply set), a
        numeric aspect is re-derived from the pair so the two scales are exactly as asked.
        """
        scale_x = min(max(float(scale_x), self.MIN_SCALE), self.MAX_SCALE)
        scale_y = min(max(float(scale_y), self.MIN_SCALE), self.MAX_SCALE)
        self._centre = QVector2D(float(centre_x), float(centre_y))
        self._zoom = scale_x
        if isinstance(self._aspect, str):
            self._y_scale = scale_y
        else:
            self._aspect = scale_x / scale_y
            self.aspectChanged.emit()
        self._home = False
        self._view_changed()

    # --------------------------------------------------------------- slots
    @Slot(float, float)
    def setViewport(self, width: float, height: float) -> None:
        """Record the widget size (logical pixels) — used by the derived ranges only.

        Drawing never depends on it: the shader derives its mapping from the camera and its
        own size. The first report resolves the home view (see ``HOME_VIEW``); later reports
        keep the scales, so nothing zooms while a window or a splitter is dragged.
        """
        if width <= 0 or height <= 0:
            return
        size = (float(width), float(height))
        if size == self._size:
            return
        self._size = size
        self._resolve_home()
        self._view_changed()

    def _resolve_home(self) -> None:
        """Turn the home view into scales for the known size (once, while still at home)."""
        if not self._home:
            return
        width, height = self._effective_size()
        x_scale = (HOME_VIEW[1] - HOME_VIEW[0]) / width
        y_scale = (HOME_VIEW[3] - HOME_VIEW[2]) / height
        if isinstance(self._aspect, str):
            self._zoom, self._y_scale = x_scale, y_scale
        else:
            aspect = float(self._aspect)
            self._zoom = max(x_scale, aspect * y_scale)   # expand, never crop
            self._y_scale = self._zoom / aspect
        if self._size is not None:
            self._home = False

    @Slot(float, float, float, float, float)
    def zoom_by(self, delta: float, u: float, v: float, width: float, height: float) -> None:
        """Zoom by ``delta`` wheel units (120 = one notch), anchored at the normalised
        cursor position ``(u, v)`` of a ``width`` x ``height`` viewport.

        The factor depends only on ``delta``, so scrolling up and down at the same position
        are exact inverses; the world point under the cursor stays put.
        """
        if not self._zoom_enabled or delta == 0:
            return
        target = min(max(self._zoom * self._zoom_step ** (delta / 120.0), self.MIN_SCALE),
                     self.MAX_SCALE)
        factor = target / self._zoom
        if factor == 1.0:
            return
        if width > 0 and height > 0:
            anchor_x = self._centre.x() + (u - 0.5) * width * self.scale_x
            anchor_y = self._centre.y() + (0.5 - v) * height * self.scale_y
        else:
            anchor_x, anchor_y = self._centre.x(), self._centre.y()
        self._zoom = target
        if isinstance(self._aspect, str):
            self._y_scale = min(max(self._y_scale * factor, self.MIN_SCALE), self.MAX_SCALE)
        if width > 0 and height > 0:
            self._centre = QVector2D(
                anchor_x - (u - 0.5) * width * self.scale_x,
                anchor_y - (0.5 - v) * height * self.scale_y,
            )
        self._home = False
        self._view_changed()

    @Slot(float, float)
    def pan_pixels(self, dx: float, dy: float) -> None:
        """Pan by a pixel displacement (screen y points down, world y points up)."""
        if not self._pan_enabled or (dx == 0.0 and dy == 0.0):
            return
        self._centre = QVector2D(
            self._centre.x() - dx * self.scale_x,
            self._centre.y() + dy * self.scale_y,
        )
        self._home = False
        self._view_changed()

    @Slot()
    def reset(self) -> None:
        """Back to the home view (centre and scales as the camera was configured)."""
        self._centre = QVector2D(0.0, 0.0)
        self._home = True
        self._resolve_home()
        self._view_changed()
