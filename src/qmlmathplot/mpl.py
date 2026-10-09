"""Hand-off to matplotlib — and through it to sympy's plotting.

This is deliberately a **second renderer**, not the one the Qt view uses, and it is a *sampled*
one: matplotlib draws polylines, so an oscillating function such as ``sin(1/x)`` aliases exactly
as it does in any sampling renderer — which is the reason the Qt side exists and evaluates the
expression per pixel instead. Fonts and antialiasing differ from the Qt view as well, and the
style mapping below is partial. What this buys is **vector output** (``.svg``/``.pdf``) and
matplotlib's ecosystem (and sympy's plotting), nothing else.

matplotlib is an *optional* dependency (the ``matplotlib`` extra): this module imports nothing
until one of its functions is called, and ``import qmlmathplot`` never pulls it in.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import sympy as sp

if TYPE_CHECKING:  # pragma: no cover - typing only; the real imports stay lazy
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from .curve import Curve
    from .plot import Plot

__all__ = ["figure_for", "save_via_matplotlib", "sympy_plot_for"]

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
    """The curves the Qt view would draw (a hidden curve is not handed over)."""
    return [plot.curves.at(index) for index in range(len(plot.curves))
            if plot.curves.at(index).visible]


def _sample(expression: str, x0: float, x1: float, samples: int) -> tuple[object, object]:
    """Uniform samples of one curve; NaN where the function is outside its domain.

    matplotlib breaks the line at NaN, which is how the hand-off keeps ``log(x)`` from being
    drawn for x < 0. Infinities (poles, ``1/x`` at 0) are turned into NaN as well, so a pole
    breaks the line instead of drawing a connector across it.
    """
    import numpy

    symbol = sp.Symbol("x")
    try:
        function = sp.lambdify(symbol, sp.sympify(expression, locals={"x": symbol}),
                               modules=["numpy"])
        with numpy.errstate(all="ignore"):        # log(x<0) etc.: NaN, not a warning storm
            xs = numpy.linspace(x0, x1, samples)
            ys = numpy.asarray(function(xs), dtype=float)
    except Exception as exc:
        # Broad on purpose: sympify, lambdify and the evaluation itself all report by raising,
        # and the caller wants one clear message naming the expression.
        raise ValueError(f"cannot sample {expression!r}: {exc}") from exc
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
    for spine in axes.spines.values():
        spine.set_color(plot.axis_color.name())
        spine.set_linewidth(plot.axis_width * PX_TO_PT)

    axes.set_axisbelow(True)                      # the Qt view paints its grid below the curves
    if plot.grid:
        axes.grid(True, color=plot.grid_color.name(),
                  linewidth=plot.grid_width * PX_TO_PT, alpha=plot.grid_alpha,
                  linestyle=plot.grid_style)
    else:
        # Passing line properties together with `False` makes matplotlib *enable* the grid.
        axes.grid(False)
    axes.tick_params(colors=plot.tick_color.name(), labelcolor=plot.text_color.name(),
                     labelsize=plot.tick_font_size * PX_TO_PT)
    axes.xaxis.label.set_fontsize(plot.font_size * PX_TO_PT)
    axes.yaxis.label.set_fontsize(plot.font_size * PX_TO_PT)
    if plot.title:
        axes.set_title(plot.title, color=plot.title_color.name(),
                       fontsize=plot.title_font_size * PX_TO_PT,
                       fontweight="bold" if plot.title_bold else "normal")

    for curve in _visible_curves(plot):
        xs, ys = _sample(curve.expression, x_range[0], x_range[1], samples)
        axes.plot(xs, ys, color=curve.color.name(), linewidth=curve.lineWidth * PX_TO_PT,
                  label=curve.label or curve.expression)
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


def sympy_plot_for(
    plot: Plot,
    *,
    xlim: Sequence[float] | None = None,
    ylim: Sequence[float] | None = None,
    samples: int = 2000,
    adaptive: bool = False,
) -> object:
    """Hand this plot to ``sympy.plotting``, uniform by default (``adaptive`` asks for sympy's.

    Returns whatever ``sympy.plotting.plot(..., show=False)`` returns (a matplotlib-backend
    ``Plot`` object in sympy 1.14), so ``handle.save(path)``/``handle.show()`` work, the window
    is the same one the matplotlib hand-off would show, and every curve keeps its colour.
    """
    _require()                                    # sympy's plotting backend is matplotlib too
    symbol = sp.Symbol("x")
    curves = _visible_curves(plot)
    if not curves:
        raise ValueError("nothing to hand to sympy: the plot has no visible curve")
    x_range = _pair(xlim, "xlim") if xlim is not None else (plot.xlim.x(), plot.xlim.y())
    y_range = _pair(ylim, "ylim") if ylim is not None else (plot.ylim.x(), plot.ylim.y())
    expressions = []
    for curve in curves:
        try:
            expressions.append(sp.sympify(curve.expression, locals={"x": symbol}))
        except Exception as exc:
            raise ValueError(f"cannot parse {curve.expression!r}: {exc}") from exc
    handle = sp.plotting.plot(
        *expressions, (symbol, x_range[0], x_range[1]), show=False,
        adaptive=adaptive,                        # see Plot.to_sympy for why False is the default
        nb_of_points=samples,
        label=[curve.label or curve.expression for curve in curves],
    )
    handle.xlim = x_range                         # the same window as the matplotlib hand-off
    handle.ylim = y_range
    for index, curve in enumerate(curves):        # per-series style (not a global line_color)
        handle[index].line_color = curve.color.name()
    return handle
