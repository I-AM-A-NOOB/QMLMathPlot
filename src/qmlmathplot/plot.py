"""Plot layer: what the view binds to — a camera, a list of curves, and the styling.

The plot owns no pixels: it is a plain QObject, so the same plot can be shown by
``PlotView``, embedded through ``MathPlotWidget`` or rendered offscreen. Styling lives here
(one property per knob, each with a notify signal) so a theme is just a dict of property
names — see ``theme``.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QColor, QVector2D

from .camera import Camera
from .curve import DEFAULT_COLOR_CYCLE, Curve, CurveListModel

__all__ = ["Plot"]


def _to_color(value: object) -> QColor:
    return QColor(value) if not isinstance(value, QColor) else QColor(value)


#: Value conversions per style kind (QML hands over whatever JS has: 1 for a bool, a string
#: for a colour, …).
_CONVERTERS = {
    bool: bool,
    float: float,
    str: str,
    QColor: _to_color,
}


def _style(kind: object, name: str, notify: Signal, default: object) -> Property:
    """One style Property, with the house rules baked in: converts, idempotent, and emits its
    notify signal only on a real change (a theme writes every knob in a row).

    ``notify`` must be the signal *object* (PySide6 needs it to wire the property up; a name
    string silently leaves the property without a notify signal, and QML bindings then never
    refresh). Emitting goes through the descriptor protocol to reach this instance's signal.
    """
    convert = _CONVERTERS.get(kind)

    def getter(self: Plot) -> object:
        return getattr(self, "_" + name, default)

    def setter(self: Plot, value: object) -> None:
        value = convert(value) if convert is not None else value  # type: ignore[operator]
        if value == getattr(self, "_" + name, default):
            return
        setattr(self, "_" + name, value)
        notify.__get__(self, type(self)).emit()

    return Property(kind, getter, setter, notify=notify)


class Plot(QObject):
    """The canvas state: a camera, the curves, and how they are dressed."""

    cameraChanged = Signal()
    curvesChanged = Signal()
    viewChanged = Signal()
    themeChanged = Signal()

    backgroundChanged = Signal()
    gridChanged = Signal()
    gridColorChanged = Signal()
    gridWidthChanged = Signal()
    gridAlphaChanged = Signal()
    gridStyleChanged = Signal()
    colorCycleChanged = Signal()
    lineWidthChanged = Signal()
    textColorChanged = Signal()
    tickColorChanged = Signal()
    tickLengthChanged = Signal()
    fontSizeChanged = Signal()
    tickFontSizeChanged = Signal()
    titleChanged = Signal()
    titleColorChanged = Signal()
    titleFontSizeChanged = Signal()
    titleBoldChanged = Signal()
    ticksVisibleChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._camera = Camera(self)
        self._curves = CurveListModel(DEFAULT_COLOR_CYCLE, self.line_width, self)
        self._camera.viewChanged.connect(self.viewChanged)
        self.cameraChanged.emit()
        self.curvesChanged.emit()

    # ------------------------------------------------------------- the model
    def _get_camera(self) -> Camera:
        return self._camera

    camera: Camera = Property(Camera, _get_camera, notify=cameraChanged)

    def _get_curves(self) -> CurveListModel:
        return self._curves

    # Declared as QObject: PySide6 cannot register a Python QAbstractListModel subclass as a
    # Qt property type (the meta-object rejects "QAbstractListModel*"), and QML only needs the
    # object to *be* a list model.
    curves: CurveListModel = Property(QObject, _get_curves, notify=curvesChanged)

    def _get_xlim(self) -> QVector2D:
        return self._camera.xlim

    def _set_xlim(self, value: object) -> None:
        self._camera.xlim = value

    def _get_ylim(self) -> QVector2D:
        return self._camera.ylim

    def _set_ylim(self, value: object) -> None:
        self._camera.ylim = value

    # Forwarded to the camera; "QVariant" so a plain (lo, hi) pair is accepted as well.
    xlim: QVector2D = Property("QVariant", _get_xlim, _set_xlim, notify=viewChanged)
    ylim: QVector2D = Property("QVariant", _get_ylim, _set_ylim, notify=viewChanged)

    @Slot(str, "QVariant", "QVariant", str, result=QObject)
    @Slot(str, result=QObject)
    def add_curve(
        self,
        expression: str,
        color: object = None,
        line_width: object = None,
        label: str = "",
    ) -> Curve:
        """Add a curve (colour and width default to the plot's cycle and ``lineWidth``)."""
        return self._curves.add_curve(expression, color, line_width, label)

    @Slot(QObject)
    def remove_curve(self, curve: Curve) -> None:
        self._curves.remove_curve(curve)

    # -------------------------------------------------------------- styling
    background: QColor = _style(QColor, "background", backgroundChanged, QColor("#14141e"))
    grid: bool = _style(bool, "grid", gridChanged, True)
    grid_color: QColor = _style(QColor, "grid_color", gridColorChanged, QColor("#2a2a3a"))
    grid_width: float = _style(float, "grid_width", gridWidthChanged, 1.0)
    grid_alpha: float = _style(float, "grid_alpha", gridAlphaChanged, 0.5)
    #: "-" | "--" | ":" | "-." (QML turns it into a dash pattern).
    grid_style: str = _style(str, "grid_style", gridStyleChanged, "-")
    text_color: QColor = _style(QColor, "text_color", textColorChanged, QColor("#c8c8d2"))
    tick_color: QColor = _style(QColor, "tick_color", tickColorChanged, QColor("#6a6a80"))
    tick_length: float = _style(float, "tick_length", tickLengthChanged, 6.0)
    font_size: float = _style(float, "font_size", fontSizeChanged, 12.0)
    tick_font_size: float = _style(float, "tick_font_size", tickFontSizeChanged, 11.0)
    title: str = _style(str, "title", titleChanged, "")
    title_color: QColor = _style(QColor, "title_color", titleColorChanged, QColor("#e8e8f0"))
    title_font_size: float = _style(float, "title_font_size", titleFontSizeChanged, 15.0)
    title_bold: bool = _style(bool, "title_bold", titleBoldChanged, True)
    ticks_visible: bool = _style(bool, "ticks_visible", ticksVisibleChanged, True)

    def _get_color_cycle(self) -> list[str]:
        return list(getattr(self, "_color_cycle", DEFAULT_COLOR_CYCLE))

    def _set_color_cycle(self, value: object) -> None:
        cycle = [str(entry) for entry in value] or [DEFAULT_COLOR_CYCLE[0]]  # type: ignore[union-attr]
        if cycle == self._get_color_cycle():
            return
        self._color_cycle = cycle
        self._curves.set_color_cycle(tuple(cycle))
        self.colorCycleChanged.emit()

    color_cycle: list[str] = Property("QVariant", _get_color_cycle, _set_color_cycle,
                                      notify=colorCycleChanged)

    def _get_line_width(self) -> float:
        return float(getattr(self, "_line_width", 1.5))

    def _set_line_width(self, value: float) -> None:
        value = float(value)
        if value == self._get_line_width():
            return
        self._line_width = value
        self._curves.set_default_line_width(value)   # default for the curves added later
        self.lineWidthChanged.emit()

    #: Default line width of new curves (logical pixels).
    line_width: float = Property(float, _get_line_width, _set_line_width,
                                 notify=lineWidthChanged)

    def _get_theme(self) -> str:
        return getattr(self, "_theme", "")

    def _set_theme(self, name: str) -> None:
        """Apply a named theme: every entry is a style property of this object."""
        name = str(name)
        if name == self._get_theme():
            return
        from .themes import resolve      # lazy: a missing themes module must not break import

        resolved = resolve(name)         # unknown name -> KeyError, theme unchanged
        for key, value in resolved.items():
            setattr(self, key, value)
        self._theme = name
        self.themeChanged.emit()

    theme: str = Property(str, _get_theme, _set_theme, notify=themeChanged)
