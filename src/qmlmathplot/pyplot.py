"""A ``matplotlib.pyplot``-flavoured scripting surface over the object API.

This module exists for scripts, notebooks and demos. It deliberately keeps Matplotlib's
*global current figure* model, which the object API refuses: applications should create
``Figure`` objects and embed ``FigureCanvasQt`` / ``PlotView`` explicitly.

The same differences as :mod:`qmlmathplot.figure` apply: one axes per figure, expressions
rather than x/y data arrays, a live Qt scene (``draw()`` has nothing to do), and an export
that is a canvas region rather than a page at a dpi.
"""

from __future__ import annotations

import sys
import warnings

from .figure import Axes, Figure

__all__ = [
    "Axes",
    "Figure",
    "annotate",
    "aspect",
    "axis",
    "axhline",
    "axvline",
    "clf",
    "close",
    "draw",
    "draw_idle",
    "figure",
    "gca",
    "gcf",
    "grid",
    "legend",
    "plot",
    "savefig",
    "sca",
    "show",
    "subplot",
    "subplots",
    "text",
    "theme",
    "tick_params",
    "tight_layout",
    "title",
    "to_image",
    "xlabel",
    "xlim",
    "xticks",
    "ylabel",
    "ylim",
    "yticks",
]

_figures: dict[int, Figure] = {}
_windows: dict[int, object] = {}
_current: Figure | None = None
_next_number = 1
#: True when pyplot created the QApplication itself (script mode). A host application that
#: already existed is never blocked on: nesting its event loop would be wrong.
_application_owned = False


def _register(figure_obj: Figure) -> Figure:
    global _current, _next_number
    if figure_obj.number is None:
        while _next_number in _figures:
            _next_number += 1
        figure_obj.number = _next_number
        _next_number += 1
    _figures[figure_obj.number] = figure_obj
    _current = figure_obj
    return figure_obj


def figure(
    num: int | Figure | None = None,
    *,
    figsize: tuple[float, float] | None = None,
    dpi: float | None = None,
    facecolor: object = None,
    **kwargs: object,
) -> Figure:
    """Create or select a figure and make it current (``plt.figure``)."""
    global _current
    if isinstance(num, Figure):
        return _register(num)
    if num is not None:
        try:
            number = int(num)
        except (TypeError, ValueError):
            raise TypeError(f"figure number must be an int or a Figure, got {num!r}") from None
        if number in _figures:
            _current = _figures[number]
            return _current
    created = Figure(figsize=figsize, dpi=dpi, facecolor=facecolor, **kwargs)
    if num is not None:
        created.number = int(num)
    return _register(created)


def gcf() -> Figure:
    """The current figure, created if necessary (``plt.gcf``)."""
    if _current is None:
        return figure()
    return _current


def gca() -> Axes:
    return gcf().gca()


def sca(axes: Axes) -> None:
    global _current
    if not isinstance(axes, Axes):
        raise TypeError(f"sca expects an Axes, got {type(axes).__name__}")
    _current = _register(axes.figure)


def plot(*args: object, **kwargs: object):
    return gca().plot(*args, **kwargs)


def xlim(*args: object, **kwargs: object) -> tuple[float, float]:
    gca().set_xlim(*args, **kwargs)
    return gca().get_xlim()


def ylim(*args: object, **kwargs: object) -> tuple[float, float]:
    gca().set_ylim(*args, **kwargs)
    return gca().get_ylim()


def title(label: object, **kwargs: object) -> None:
    gca().set_title(label, **kwargs)


def xlabel(label: object, **kwargs: object) -> None:
    gca().set_xlabel(label, **kwargs)


def ylabel(label: object, **kwargs: object) -> None:
    gca().set_ylabel(label, **kwargs)


def grid(visible: bool | None = True, **kwargs: object) -> None:
    gca().grid(visible, **kwargs)


def aspect(value: object, adjustable: object = None) -> None:
    gca().set_aspect(value, adjustable)


def axis(*args: object, **kwargs: object) -> None:
    """``axis("off")`` / ``axis(False)`` hide the axes and labels; ``"on"`` restores them."""
    if not args:
        raise TypeError("axis() needs 'on' or 'off'")
    value = args[0]
    if value in ("off", False, 0):
        gca().set_axis_off()
    elif value in ("on", True, 1):
        gca().set_axis_on()
    else:
        raise NotImplementedError(
            "axis() only toggles the axes here; limits and ticks have their own methods"
        )


def theme(name: str) -> None:
    """Apply a bundled Matplotlib style sheet to the current figure."""
    gca().set_theme(name)


def legend(*args: object, **kwargs: object) -> None:
    gca().legend(*args, **kwargs)


def annotate(
    text_label: object,
    xy: object,
    xytext: object = (24, -18),
    **kwargs: object,
):
    return gca().annotate(text_label, xy, xytext, **kwargs)


def text(x: float, y: float, s: object, **kwargs: object):
    return gca().text(x, y, s, **kwargs)


def xticks(ticks: object, labels: object = None, **kwargs: object) -> None:
    gca().set_xticks(ticks, labels, **kwargs)


def yticks(ticks: object, labels: object = None, **kwargs: object) -> None:
    gca().set_yticks(ticks, labels, **kwargs)


def tick_params(axis: str = "both", **kwargs: object) -> None:
    gca().tick_params(axis, **kwargs)


def axhline(y: float = 0.0, **kwargs: object):
    return gca().axhline(y, **kwargs)


def axvline(x: float = 0.0, **kwargs: object):
    return gca().axvline(x, **kwargs)


def subplots(
    nrows: int = 1,
    ncols: int = 1,
    *,
    figsize: tuple[float, float] | None = None,
    dpi: float | None = None,
    facecolor: object = None,
    squeeze: bool = True,
    **kwargs: object,
) -> tuple[Figure, Axes]:
    """One figure and one axes. More cells are a Qt layout of several figures."""
    if int(nrows) * int(ncols) != 1:
        raise NotImplementedError(
            "QMLMathPlot has one view per widget: build several Figure objects in a Qt layout "
            "instead of a subplot grid"
        )
    created = figure(figsize=figsize, dpi=dpi, facecolor=facecolor)
    return created, created.gca()


def subplot(*args: object, **kwargs: object) -> Axes:
    return gcf().add_subplot(*args, **kwargs)


def clf() -> None:
    gcf().clf()


def draw() -> None:
    """No-op: the Qt scene is live."""


def draw_idle() -> None:
    """No-op: the Qt scene is live."""


def tight_layout(*args: object, **kwargs: object) -> None:
    """No-op: one view fills its widget; there are no margins to pack."""


def _ensure_application():
    """A ``QApplication`` (QtWidgets) or a clear error, without importing it at module load."""
    global _application_owned
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        _application_owned = True
        return QApplication(sys.argv[:1]), True
    if not isinstance(app, QApplication):
        raise RuntimeError(
            "pyplot needs a QApplication: it hosts Qt widgets; a bare QGuiApplication cannot "
            "create them"
        )
    return app, False


def _ensure_window(figure_obj: Figure):
    """Give ``figure_obj`` a top-level window/canvas, reusing an existing canvas."""
    global _current
    if figure_obj.number is None:
        _register(figure_obj)
    number = figure_obj.number
    assert number is not None
    existing = _windows.get(number)
    if existing is not None:
        return existing
    if figure_obj.canvas is not None:
        window = figure_obj.canvas.window()
        _windows[number] = window
        return window

    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from .canvas import FigureCanvasQt

    window = QWidget()
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    canvas = FigureCanvasQt(figure_obj, window)
    layout.addWidget(canvas)
    window.setWindowTitle(figure_obj.plot.title or f"QMLMathPlot — Figure {number}")
    window.resize(*figure_obj.get_size_pixels())
    window.show()
    _windows[number] = window
    _current = figure_obj
    return window


def show(block: bool | None = None) -> None:
    """Show every open figure and optionally run the Qt event loop.

    ``block=None`` (the default) blocks when this call created the ``QApplication`` (script
    mode) and returns immediately when an application already existed (an embedded host), so
    ``plt.show()`` cannot nest an event loop inside a running one.
    """
    if not _figures:
        return
    app, created = _ensure_application()
    for figure_obj in list(_figures.values()):
        _ensure_window(figure_obj)
    for window in _windows.values():
        window.show()  # type: ignore[attr-defined]
        window.raise_()  # type: ignore[attr-defined]

    should_block = _application_owned if block is None else bool(block)
    if not should_block:
        return
    if not created and not _application_owned:
        warnings.warn(
            "show(block=True) was called while a QApplication already exists; returning "
            "instead of nesting an event loop (use block=False in embedded hosts)",
            RuntimeWarning,
            stacklevel=2,
        )
        return
    app.exec()


def close(fig: Figure | int | str | None = None) -> None:
    """Close a figure (or ``"all"``); the current one by default."""
    global _current
    if fig == "all":
        targets = list(_figures.values())
    elif fig is None:
        if _current is None:
            return
        targets = [_current]
    elif isinstance(fig, Figure):
        targets = [fig]
    else:
        found = _figures.get(int(fig))
        targets = [found] if found is not None else []

    for figure_obj in targets:
        number = figure_obj.number
        if number is None:
            continue
        window = _windows.pop(number, None)
        if window is not None:
            window.close()  # type: ignore[attr-defined]
            window.deleteLater()  # type: ignore[attr-defined]
        _figures.pop(number, None)
        if figure_obj.canvas is not None:
            figure_obj.canvas = None
    if _current in targets:
        _current = next(reversed(_figures.values()), None) if _figures else None


def savefig(path: str, **kwargs: object) -> None:
    # The Qt raster backend needs QtWidgets; the matplotlib backend does not.
    if kwargs.get("backend", "qt") == "qt":
        _ensure_application()
    gcf().savefig(path, **kwargs)


def to_image(**kwargs: object):
    _ensure_application()
    return gcf().to_image(**kwargs)
