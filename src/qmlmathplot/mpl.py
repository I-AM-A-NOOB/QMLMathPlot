"""Hand-off to matplotlib (a second, sampled renderer).

This is deliberately a **second renderer**, not the one the Qt view uses, and it is a *sampled*
one: matplotlib draws polylines, so an oscillating function such as ``sin(1/x)`` aliases exactly
as it does in any sampling renderer — which is the reason the Qt side exists and evaluates the
expression per pixel instead. Fonts and antialiasing differ from the Qt view as well, and the
style mapping below is partial. What this buys is **vector output** (``.svg``/``.pdf``) and
matplotlib's ecosystem, nothing else. Sampling uses the curve's ``Program`` (<code>numpy</code>
source), so the hand-off needs no SymPy and works with programs built anywhere. Implicit
curves are skipped (a polyline cannot represent a level set).

matplotlib is an *optional* dependency (the ``matplotlib`` extra): this module imports nothing
until one of its functions is called, and ``import qmlmathplot`` never pulls it in.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only; the real imports stay lazy
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from .curve import Curve
    from .plot import Plot

__all__ = ["figure_for", "save_via_matplotlib"]

#: Logical pixels to points (96 px/inch, 72 pt/inch): our sizes are in px, matplotlib's in pt.
PX_TO_PT = 72.0 / 96.0
#: The DPI the hand-off assumes for sizes that only exist in pixels (the live viewport).
DEFAULT_DPI = 100.0
_EXTRA_HINT = (
    "the matplotlib hand-off needs the optional extra: pip install 'qmlmathplot[matplotlib]'"
)


def _require() -> tuple[type[Figure], type[FigureCanvasAgg]]:
    """The matplotlib classes for a headless figure, or a clear error naming the extra."""
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError as exc:                    # no matplotlib installed
        raise ImportError(_EXTRA_HINT) from exc
    return Figure, FigureCanvasAgg


def _pair(value: Sequence[float], name: str) -> tuple[float, float]:
    try:
        lo, hi = float(value[0]), float(value[1])
    except (TypeError, IndexError, KeyError):
        raise ValueError(f"{name} must be a (min, max) pair, got {value!r}") from None
    if not hi > lo:
        raise ValueError(f"{name} must be increasing, got {value!r}")
    return lo, hi


def _visible_curves(plot: Plot) -> list[Curve]:
    """The curves the Qt view would draw (a hidden curve is not handed over).

    Implicit curves (the level set ``F(x, y) = 0``) are **skipped**: a sampled polyline cannot
    come from an implicit expression (the faithful route would be a ``contour`` on a grid, i.e. a
    different renderer with a grid resolution), and this hand-off refuses to pretend. A plot made
    of implicit curves only is therefore empty here — use the Qt export for it.
    """
    return [plot.curves.at(index) for index in range(len(plot.curves))
            if plot.curves.at(index).visible and not plot.curves.at(index).implicit]


def _sample(curve: Curve, x0: float, x1: float, samples: int) -> tuple[object, object]:
    """Uniform samples of one curve; NaN where the function is outside its domain.

    matplotlib breaks the line at NaN, which is how the hand-off keeps ``log(x)`` from being
    drawn for x < 0. Infinities (poles) become NaN as well, so a pole breaks the line instead
    of drawing a connector across it. The function comes from ``Program.numpy`` — the curve
    never parses text again and SymPy is not involved.
    """
    import numpy

    program = curve.program
    if program is None:
        raise ValueError(f"{curve.expression!r} has no program yet")
    if program.kind == "data":
        points = curve.points
        if points is None:
            raise ValueError("a data curve has no points")
        return numpy.asarray(points[0]), numpy.asarray(points[1])
    try:
        function = program.compile(numpy=True)
        with numpy.errstate(all="ignore"):        # log(x<0) etc.: NaN, not a warning storm
            xs = numpy.linspace(x0, x1, samples)
            ys = numpy.asarray(function(xs), dtype=float)
    except Exception as exc:
        raise ValueError(f"cannot sample {curve.expression!r}: {exc}") from exc
    if ys.ndim == 0:                              # a constant expression evaluates to a scalar
        ys = numpy.full(xs.shape, float(ys))
    ys[~numpy.isfinite(ys)] = numpy.nan
    return xs, ys


def figure_for(
    plot: Plot,
    *,
    xlim: Sequence[float] | None = None,
    ylim: Sequence[float] | None = None,
    samples: int = 2000,
    adjustable: str = "box",
    size: tuple[float, float] | None = None,
    dpi: float = DEFAULT_DPI,
    transparent: bool = False,
) -> Figure:
    """A matplotlib ``Figure`` with one Axes, reflecting this plot as far as it maps.

    ``xlim`` / ``ylim`` default to the camera's visible range; ``size`` is the figure size in
    device pixels (default: the live viewport) and ``dpi`` turns it into inches. ``adjustable``
    follows the export's modes: ``"box"`` and ``"datalim"`` map to matplotlib's own, and
    ``"stretch"`` (independent scales) becomes matplotlib's ``"auto"`` aspect.
    """
    if adjustable not in ("box", "datalim", "stretch"):
        raise ValueError(f'adjustable must be "box", "datalim" or "stretch", got {adjustable!r}')
    Figure, FigureCanvasAgg = _require()
    dpi = float(dpi)
    if not dpi > 0:
        raise ValueError(f"dpi must be positive, got {dpi!r}")

    x_range = _pair(xlim, "xlim") if xlim is not None else (plot.xlim.x(), plot.xlim.y())
    y_range = _pair(ylim, "ylim") if ylim is not None else (plot.ylim.x(), plot.ylim.y())
    if size is None:                              # the live viewport, at the hand-off's dpi
        viewport = plot.camera.viewport
        size = (viewport.x(), viewport.y())
    inches = (size[0] / dpi, size[1] / dpi)

    figure = Figure(figsize=inches, dpi=dpi)
    FigureCanvasAgg(figure)                       # headless canvas: no pyplot, no GUI backend
    axes = figure.add_subplot()
    axes.set_facecolor(plot.background.name())
    figure.patch.set_facecolor("none" if transparent else plot.background.name())
    axes.set_xlim(*x_range)
    axes.set_ylim(*y_range)

    if adjustable == "stretch":                   # independent scales: matplotlib's "auto"
        axes.set_aspect("auto")
    else:
        aspect = plot.camera.aspect               # "auto" | number, same convention as ours
        axes.set_aspect("auto" if isinstance(aspect, str) else float(aspect),
                        adjustable=adjustable)

    if plot.axes_position == "zero":              # our axes through world (0,0), no box
        axes.spines["left"].set_position("zero")
        axes.spines["bottom"].set_position("zero")
        axes.spines["top"].set_visible(False)
        axes.spines["right"].set_visible(False)
    elif plot.axes_position == "box":
        for side, visible in (
            ("top", plot.spine_top), ("bottom", plot.spine_bottom),
            ("left", plot.spine_left), ("right", plot.spine_right),
        ):
            axes.spines[side].set_visible(visible)
    for spine in axes.spines.values():
        spine.set_color(plot.axis_color.name())
        spine.set_linewidth(plot.axis_width * PX_TO_PT)

    if plot.xticks:
        axes.set_xticks([float(value) for value, _label in plot.xticks],
                        labels=[label for _value, label in plot.xticks])
    if plot.yticks:
        axes.set_yticks([float(value) for value, _label in plot.yticks],
                        labels=[label for _value, label in plot.yticks])

    axes.set_axisbelow(True)                      # the Qt view paints its grid below the curves
    grid_kwargs = {
        "color": plot.grid_color.name(),
        "linewidth": plot.grid_width * PX_TO_PT,
        "alpha": plot.grid_alpha,
        "linestyle": plot.grid_style,
    }
    if plot.grid:
        axes.grid(False)
        if plot.grid_x:
            axes.xaxis.grid(True, **grid_kwargs)
        if plot.grid_y:
            axes.yaxis.grid(True, **grid_kwargs)
    else:
        axes.grid(False)
    axes.tick_params(
        colors=plot.tick_color.name(),
        labelcolor=plot.text_color.name(),
        labelsize=plot.tick_font_size * PX_TO_PT,
        width=plot.tick_width * PX_TO_PT,
        pad=plot.tick_pad * PX_TO_PT,
        top=plot.ticks_top,
        right=plot.ticks_right,
    )
    if plot.minor_ticks:
        axes.minorticks_on()
    axes.xaxis.label.set_fontsize(plot.font_size * PX_TO_PT)
    axes.yaxis.label.set_fontsize(plot.font_size * PX_TO_PT)
    if plot.xlabel:
        axes.set_xlabel(plot.xlabel, color=plot.text_color.name())
    if plot.ylabel:
        axes.set_ylabel(plot.ylabel, color=plot.text_color.name())
    if plot.title:
        axes.set_title(plot.title, color=plot.title_color.name(),
                       fontsize=plot.title_font_size * PX_TO_PT,
                       fontweight="bold" if plot.title_bold else "normal")

    for index in range(plot.annotations.rowCount()):
        annotation = plot.annotations.at(index)
        if not annotation.visible:
            continue
        options: dict[str, object] = {
            "xycoords": "data",
            "textcoords": "offset points",
            "xytext": (annotation.xytext.x(), annotation.xytext.y()),
            "color": annotation.color.name(),
            "fontsize": annotation.fontSize * PX_TO_PT,
        }
        if annotation.arrow:
            options["arrowprops"] = {"arrowstyle": "->", "color": annotation.color.name()}
        axes.annotate(annotation.text, (annotation.xy.x(), annotation.xy.y()), **options)

    for index in range(plot.guides.rowCount()):
        guide = plot.guides.at(index)
        if not guide.visible:
            continue
        if guide.kind == "h":
            axes.axhline(guide.value, color=guide.color.name(),
                         linewidth=guide.width * PX_TO_PT, linestyle=guide.style)
        else:
            axes.axvline(guide.value, color=guide.color.name(),
                         linewidth=guide.width * PX_TO_PT, linestyle=guide.style)

    for curve in _visible_curves(plot):
        xs, ys = _sample(curve, x_range[0], x_range[1], samples)
        axes.plot(xs, ys, color=curve.color.name(), linewidth=curve.lineWidth * PX_TO_PT,
                  label=curve.label or curve.expression)
    if plot.legend and any(curve.label for curve in _visible_curves(plot)):
        axes.legend(loc=plot.legend_position)
    return figure


def save_via_matplotlib(
    plot: Plot,
    path: str,
    *,
    xlim: Sequence[float] | None = None,
    ylim: Sequence[float] | None = None,
    samples: int = 2000,
    adjustable: str = "box",
    width: float | None = None,
    height: float | None = None,
    dpi: float = DEFAULT_DPI,
    transparent: bool = False,
) -> None:
    """Write the hand-off figure with matplotlib's own ``savefig`` (svg/pdf/png/…)."""
    size = None
    if width is not None or height is not None:
        viewport = plot.camera.viewport
        size = (float(width) if width is not None else viewport.x(),
                float(height) if height is not None else viewport.y())
    figure = figure_for(plot, xlim=xlim, ylim=ylim, samples=samples, adjustable=adjustable,
                        size=size, dpi=dpi, transparent=transparent)
    figure.savefig(str(path), transparent=transparent)
