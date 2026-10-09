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
from .themes import names, resolve

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
    aspectChanged = Signal()        # forwarded from the camera (the forwarded `aspect`)
    themeChanged = Signal()

    backgroundChanged = Signal()
    axisColorChanged = Signal()
    axisWidthChanged = Signal()
    axesPositionChanged = Signal()
    available_themesChanged = Signal()
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
        self._theme = ""
        self._curves = CurveListModel(DEFAULT_COLOR_CYCLE, self.line_width, self)
        self._camera.viewChanged.connect(self.viewChanged)
        self._camera.aspectChanged.connect(self.aspectChanged)
        self.cameraChanged.emit()
        self.curvesChanged.emit()
        # matplotlib's look out of the box: the theme file is the single source of truth, so
        # the fallbacks above and the theme agree instead of drifting apart.
        self._set_theme("default")

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

    def _get_aspect(self) -> str | float:
        return self._camera.aspect

    def _set_aspect(self, value: object) -> None:
        self._camera.aspect = value

    # Forwarded to the camera; "QVariant" so a plain (lo, hi) pair is accepted as well.
    xlim: QVector2D = Property("QVariant", _get_xlim, _set_xlim, notify=viewChanged)
    ylim: QVector2D = Property("QVariant", _get_ylim, _set_ylim, notify=viewChanged)
    # `aspect` is forwarded too because a QML *binding* cannot reach through an object chain
    # (`plot.camera.aspect: 1.0` is rejected by QML), while a direct property binds fine.
    aspect: str | float = Property("QVariant", _get_aspect, _set_aspect, notify=aspectChanged)

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

    # --------------------------------------------------------------- export
    @Slot(result="QVariant")
    def to_image(
        self,
        *,
        xlim: object = None,
        ylim: object = None,
        width: float | None = None,
        height: float | None = None,
        dpi: float = 1.0,
        adjustable: str | None = None,
        transparent: bool = False,
    ) -> QImage:
        """Render this canvas region offscreen and return it (device pixels), see
        ``docs/api-design.md`` §9. QML calls it with no arguments: the live view, its own size.
        """
        from .export import render      # lazy: a pure-QML app never needs QtWidgets

        return render(self, xlim=xlim, ylim=ylim, width=width, height=height, dpi=dpi,
                      adjustable=adjustable, transparent=transparent)

    @Slot(str)
    def savefig(self, path: str, **kwargs: object) -> None:
        """Write :meth:`to_image` to ``path`` (the format comes from the suffix); QML passes a
        path only."""
        if not self.to_image(**kwargs).save(str(path)):
            raise OSError(f"could not write the export to {path!r}")

    # -------------------------------------------------------------- styling
    # The fallbacks repeat matplotlib's default style (themes/default.mplstyle, applied in
    # __init__) so a plot looks the same whether or not a theme was applied.
    background: QColor = _style(QColor, "background", backgroundChanged, QColor("white"))
    grid: bool = _style(bool, "grid", gridChanged, False)
    grid_color: QColor = _style(QColor, "grid_color", gridColorChanged, QColor("#b0b0b0"))
    grid_width: float = _style(float, "grid_width", gridWidthChanged, 1.07)
    grid_alpha: float = _style(float, "grid_alpha", gridAlphaChanged, 1.0)
    #: "-" | "--" | ":" | "-." (QML turns it into a dash pattern).
    grid_style: str = _style(str, "grid_style", gridStyleChanged, "-")
    axis_color: QColor = _style(QColor, "axis_color", axisColorChanged, QColor("black"))
    axis_width: float = _style(float, "axis_width", axisWidthChanged, 1.07)
    text_color: QColor = _style(QColor, "text_color", textColorChanged, QColor("black"))
    tick_color: QColor = _style(QColor, "tick_color", tickColorChanged, QColor("black"))
    tick_length: float = _style(float, "tick_length", tickLengthChanged, 4.67)
    font_size: float = _style(float, "font_size", fontSizeChanged, 13.33)
    tick_font_size: float = _style(float, "tick_font_size", tickFontSizeChanged, 13.33)
    title: str = _style(str, "title", titleChanged, "")
    title_color: QColor = _style(QColor, "title_color", titleColorChanged, QColor("black"))
    title_font_size: float = _style(float, "title_font_size", titleFontSizeChanged, 16.0)
    title_bold: bool = _style(bool, "title_bold", titleBoldChanged, False)
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
        return float(getattr(self, "_line_width", 2.0))

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

    def _get_axes_position(self) -> str:
        return getattr(self, "_axes_position", "zero")

    def _set_axes_position(self, value: str) -> None:
        value = str(value)
        if value not in ("zero", "edge"):
            raise ValueError(f'axesPosition must be "zero" or "edge", got {value!r}')
        if value == self._get_axes_position():
            return
        self._axes_position = value
        self.axesPositionChanged.emit()

    #: Where the ticks ride: the axes through world (0,0), or the item's edges.
    axes_position: str = Property(str, _get_axes_position, _set_axes_position,
                                  notify=axesPositionChanged)

    def _get_available_themes(self) -> list[str]:
        return list(names())

    #: Theme names a host can offer (``themes.names()``); read-only.
    available_themes: list[str] = Property("QVariant", _get_available_themes,
                                          notify=available_themesChanged)

    def _get_theme(self) -> str:
        return getattr(self, "_theme", "")

    def _set_theme(self, name: str) -> None:
        """Apply a named theme: every entry is a style property of this object.

        Assigning a theme always applies it — that is what makes ``plot.theme = "default"`` a
        reset back to matplotlib's look. The style setters emit only on a real change, so a
        re-application does not churn. An unknown name raises ``KeyError`` before applying
        anything, leaving the current look alone.
        """
        name = str(name)
        resolved = resolve(name)
        for key, value in resolved.items():
            setattr(self, key, value)
        if name == self._get_theme():
            return
        self._theme = name
        self.themeChanged.emit()

    theme: str = Property(str, _get_theme, _set_theme, notify=themeChanged)
