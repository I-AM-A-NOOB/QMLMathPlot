"""A small Matplotlib-shaped facade over :class:`~qmlmathplot.plot.Plot`.

This is a *compatibility layer*, not a second core. The core model is still a camera on an
infinite canvas with one view per widget; ``Figure`` and ``Axes`` are thin names over that
model so matplotlib-shaped code can be ported without pretending QMLMathPlot has a bounded
page, an axes box or a redraw loop.

What maps:

============================  ======================================================
Matplotlib                    Here
============================  ======================================================
``fig, ax = plt.subplots()``  one ``Figure`` / one ``Axes`` (1x1 only)
``ax.plot("sin(x)")``         ``Plot.add_curve``; returns the ``Curve`` objects
``set_xlim`` / ``set_ylim``    ``camera.xlim`` / ``ylim``
``set_title`` / labels        ``Plot.title`` / ``xlabel`` / ``ylabel``
``grid`` / ``set_aspect``     ``Plot.grid`` / ``camera.aspect``
``savefig`` / ``to_image``    the same region-based export as ``Plot``
============================  ======================================================

What deliberately does not map: ``plot(x, y)`` data arrays (the native artist is an
expression; use :meth:`Plot.to_matplotlib` for sampled data), multiple axes/subplots (use a Qt
layout of several ``Figure`` objects), the axes box/spines, vector output from the Qt renderer,
and a redraw loop. Unsupported methods raise ``NotImplementedError`` with the reason.
"""

from __future__ import annotations

from collections.abc import Sequence

import sympy as sp

from .curve import Curve
from .plot import Plot

__all__ = ["Axes", "Figure"]


#: matplotlib's default figure size in inches, kept so ``figsize`` behaves familiar.
DEFAULT_FIGSIZE = (6.4, 4.8)
DEFAULT_DPI = 100.0


def _is_expression(value: object) -> bool:
    return isinstance(value, (str, sp.Basic))


def _limits(
    args: tuple[object, ...],
    kwargs: dict[str, object],
    lo_name: str,
    hi_name: str,
    current: tuple[float, float],
) -> tuple[float, float]:
    """Read matplotlib's ``(lo, hi)`` / ``lo, hi`` / ``left=`` forms."""
    lo = kwargs.pop(lo_name, None)
    hi = kwargs.pop(hi_name, None)
    if len(args) == 1:
        pair = args[0]
        try:
            lo, hi = pair  # type: ignore[misc]
        except (TypeError, ValueError):
            raise TypeError(f"expected a (min, max) pair, got {pair!r}") from None
    elif len(args) == 2:
        lo, hi = args
    elif len(args) > 2:
        raise TypeError("expected one (min, max) pair, or two numbers")
    if kwargs:
        raise TypeError(f"unsupported limit keyword(s): {', '.join(sorted(kwargs))}")
    lo = current[0] if lo is None else float(lo)
    hi = current[1] if hi is None else float(hi)
    if not hi > lo:
        raise ValueError(f"limits must be increasing, got {(lo, hi)!r}")
    return lo, hi


def _unsupported(what: str, why: str) -> NotImplementedError:
    return NotImplementedError(f"{what} is not part of QMLMathPlot: {why}")


class Axes:
    """Matplotlib-shaped view of the single :class:`Plot` in a :class:`Figure`.

    It is intentionally not an artist container: the camera, curves and style live on the
    ``Plot``, and every setter forwards there.
    """

    def __init__(self, figure: Figure) -> None:
        self._figure = figure

    # ------------------------------------------------------------- identity
    @property
    def figure(self) -> Figure:
        return self._figure

    @property
    def model(self) -> Plot:
        """The underlying plot model (``Axes.plot`` is the Matplotlib-shaped method)."""
        return self._figure.plot

    def get_figure(self) -> Figure:
        return self._figure

    def __repr__(self) -> str:
        return f"<qmlmathplot.Axes figure={self._figure.number}>"

    # ------------------------------------------------------------ artists
    def plot(self, *args: object, **kwargs: object) -> list[Curve]:
        """Add one or more **expressions** and return the curves.

        ``ax.plot("sin(x)")`` and ``ax.plot(sp.sin(x))`` are the native form. Passing x/y
        arrays is refused with an explanation: a data polyline is a different artist (and the
        Qt renderer evaluates expressions per pixel), so sampled data should go through
        :meth:`Plot.to_matplotlib`.
        """
        expressions = self._expressions(args)
        color = kwargs.pop("color", kwargs.pop("c", None))
        line_width = kwargs.pop("linewidth", kwargs.pop("lw", None))
        label = kwargs.pop("label", None)
        visible = kwargs.pop("visible", None)
        if kwargs:
            raise TypeError(
                "unsupported plot option(s): "
                + ", ".join(sorted(kwargs))
                + " (mapped: color/c, linewidth/lw, label, visible)"
            )
        if len(expressions) > 1 and (color is not None or label is not None):
            raise ValueError("color/label apply to one expression; pass one expression per curve")
        curves: list[Curve] = []
        for expression in expressions:
            curve = self.model.add_curve(
                expression,
                color=color if len(expressions) == 1 else None,
                line_width=line_width if len(expressions) == 1 else None,
                label="" if label is None else str(label),
            )
            if visible is not None:
                curve.visible = bool(visible)
            curves.append(curve)
        return curves

    @staticmethod
    def _expressions(args: tuple[object, ...]) -> list[str | sp.Basic]:
        if not args:
            raise TypeError("plot() needs at least one expression")
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            values = list(args[0])
        else:
            values = list(args)
        if not values or not all(_is_expression(value) for value in values):
            raise _unsupported(
                "plot(x, y)",
                "the native artist is an expression, not a sampled x/y polyline; pass "
                "plot('sin(x)') or a sympy expression, or hand the plot to matplotlib "
                "for data arrays",
            )
        return values  # type: ignore[return-value]

    # -------------------------------------------------------------- limits
    def set_xlim(self, *args: object, **kwargs: object) -> None:
        current = self.get_xlim()
        lo, hi = _limits(args, dict(kwargs), "left", "right", current)
        self.model.xlim = (lo, hi)

    def get_xlim(self) -> tuple[float, float]:
        xlim = self.model.camera.xlim
        return (xlim.x(), xlim.y())

    def set_ylim(self, *args: object, **kwargs: object) -> None:
        current = self.get_ylim()
        lo, hi = _limits(args, dict(kwargs), "bottom", "top", current)
        self.model.ylim = (lo, hi)

    def get_ylim(self) -> tuple[float, float]:
        ylim = self.model.camera.ylim
        return (ylim.x(), ylim.y())

    # ---------------------------------------------------------------- text
    def set_title(
        self,
        label: object,
        fontdict: dict[str, object] | None = None,
        loc: str | None = None,
        **kwargs: object,
    ) -> None:
        self.model.title = "" if label is None else str(label)
        options = {**(fontdict or {}), **kwargs}
        if "color" in options:
            self.model.title_color = options["color"]  # type: ignore[assignment]
        size = options.get("fontsize", options.get("size"))
        if size is not None:
            self.model.title_font_size = float(size)  # type: ignore[arg-type]
        weight = options.get("fontweight", options.get("weight"))
        if weight is not None:
            self.model.title_bold = str(weight).lower() in {"bold", "heavy", "black"}
        # `loc` positions a title inside a bounded axes; there is no page here.

    def get_title(self) -> str:
        return self.model.title

    def set_xlabel(self, label: object, **kwargs: object) -> None:
        self.model.xlabel = "" if label is None else str(label)

    def get_xlabel(self) -> str:
        return self.model.xlabel

    def set_ylabel(self, label: object, **kwargs: object) -> None:
        self.model.ylabel = "" if label is None else str(label)

    def get_ylabel(self) -> str:
        return self.model.ylabel

    # -------------------------------------------------------------- dressing
    def grid(self, visible: bool | None = True, **kwargs: object) -> None:
        self.model.grid = True if visible is None else bool(visible)
        if "color" in kwargs:
            self.model.grid_color = kwargs["color"]  # type: ignore[assignment]
        width = kwargs.get("linewidth", kwargs.get("lw"))
        if width is not None:
            self.model.grid_width = float(width)  # type: ignore[arg-type]
        if "alpha" in kwargs:
            self.model.grid_alpha = float(kwargs["alpha"])  # type: ignore[arg-type]
        style = kwargs.get("linestyle", kwargs.get("ls"))
        if style is not None:
            self.model.grid_style = str(style)

    def set_aspect(self, aspect: object, adjustable: object = None) -> None:
        """``"auto"`` or a number; ``"equal"`` is ``1.0``.

        Matplotlib's ``adjustable`` ('box' letterboxing) needs an axes box inside a page,
        which this model does not have: the camera simply expands instead.
        """
        if aspect is None or aspect == "auto":
            self.model.camera.aspect = "auto"
        elif aspect == "equal":
            self.model.camera.aspect = 1.0
        elif aspect == "scaled":
            raise _unsupported("aspect='scaled'", "the camera has one scale ratio, not a data box")
        else:
            self.model.camera.aspect = float(aspect)  # type: ignore[arg-type]

    def set_facecolor(self, color: object) -> None:
        self.model.background = color  # type: ignore[assignment]

    def set_axis_off(self) -> None:
        self.model.ticks_visible = False

    def set_axis_on(self) -> None:
        self.model.ticks_visible = True

    def set_theme(self, name: str) -> None:
        self.model.theme = name

    def clear(self) -> None:
        self._figure.clf()

    # ------------------------------------------------------ explicitly absent
    def legend(self, *args: object, **kwargs: object) -> None:
        raise _unsupported(
            "legend()", "chrome belongs to the host: use Curve.label and a PlotView.overlay"
        )

    def set_xscale(self, value: object, **kwargs: object) -> None:
        raise _unsupported("log axes", "the shader mapping is linear today")

    def set_yscale(self, value: object, **kwargs: object) -> None:
        raise _unsupported("log axes", "the shader mapping is linear today")

    def annotate(self, *args: object, **kwargs: object) -> None:
        raise _unsupported("annotate()", "annotations are not implemented")

    def text(self, *args: object, **kwargs: object) -> None:
        raise _unsupported("text()", "annotations are not implemented")

    def set_xticks(self, *args: object, **kwargs: object) -> None:
        raise _unsupported("set_xticks()", "ticks are automatic nice numbers today")

    def set_yticks(self, *args: object, **kwargs: object) -> None:
        raise _unsupported("set_yticks()", "ticks are automatic nice numbers today")


class Figure:
    """One plot, presented with the shape of a Matplotlib ``Figure``.

    There is exactly one :class:`Axes` per figure; several panels are several ``Figure``
    objects in a Qt layout, not a ``GridSpec``. ``canvas`` is set by
    :class:`~qmlmathplot.canvas.FigureCanvasQt` when one is attached.
    """

    def __init__(
        self,
        plot: Plot | None = None,
        *,
        figsize: Sequence[float] | None = None,
        dpi: float | None = None,
        facecolor: object = None,
        number: int | None = None,
        **kwargs: object,
    ) -> None:
        self._plot = plot if plot is not None else Plot()
        if facecolor is not None:
            self._plot.background = facecolor  # type: ignore[assignment]
        self._axes = Axes(self)
        self._figsize = (
            DEFAULT_FIGSIZE if figsize is None else (float(figsize[0]), float(figsize[1]))
        )
        self._dpi = DEFAULT_DPI if dpi is None else float(dpi)
        self.number = number
        self.canvas: "FigureCanvasQt | None" = None
        # Matplotlib accepts many more knobs; the ones that describe a page (edgecolor,
        # frameon, tight_layout, constrained_layout, …) have no meaning on an infinite canvas.

    # ------------------------------------------------------------- identity
    @property
    def plot(self) -> Plot:
        return self._plot

    @property
    def camera(self):
        return self._plot.camera

    @property
    def axes(self) -> list[Axes]:
        return [self._axes]

    def get_axes(self) -> list[Axes]:
        return self.axes

    def gca(self) -> Axes:
        return self._axes

    def add_subplot(self, *args: object, **kwargs: object) -> Axes:
        """The single (1, 1, 1) axes. More cells are a Qt layout of ``Figure`` objects."""
        spec = ()
        if len(args) == 1 and isinstance(args[0], str) and args[0] in {"111", "1,1,1"}:
            spec = (1, 1, 1)
        elif len(args) == 1:
            value = args[0]
            if value == 111:
                spec = (1, 1, 1)
            elif isinstance(value, (tuple, list)):
                try:
                    spec = tuple(int(part) for part in value)
                except (TypeError, ValueError):
                    spec = ()
        elif len(args) == 3:
            try:
                spec = tuple(int(part) for part in args)
            except (TypeError, ValueError):
                spec = ()
        elif not args:
            spec = (1, 1, 1)
        if spec != (1, 1, 1):
            raise _unsupported(
                "multiple subplots",
                "one view per widget; build several Figure objects in a Qt layout",
            )
        if kwargs.get("projection", None) not in (None, "rectilinear"):
            raise _unsupported("polar/3-D subplots", "this is a Cartesian function plotter")
        return self._axes

    def add_axes(self, *args: object, **kwargs: object) -> Axes:
        raise _unsupported(
            "add_axes()", "there is no axes box; the plot fills its widget"
        )

    # --------------------------------------------------------------- export
    def savefig(self, path: str, **kwargs: object) -> None:
        self._plot.savefig(str(path), **kwargs)

    def to_image(self, **kwargs: object):
        return self._plot.to_image(**kwargs)

    def to_matplotlib(self, **kwargs: object):
        return self._plot.to_matplotlib(**kwargs)

    def show(self) -> None:
        """Show this figure, creating a window/canvas if it has none."""
        if self.canvas is not None:
            window = self.canvas.window()
            window.show()
            window.raise_()
            return
        from . import pyplot

        window = pyplot._ensure_window(self)
        window.show()
        window.raise_()

    # --------------------------------------------------------------- state
    def clf(self, keep_observers: object = False) -> None:
        curves = self._plot.curves
        while len(curves):
            curves.remove_curve(curves.at(0))

    def clear(self) -> None:
        self.clf()

    def get_size_inches(self) -> tuple[float, float]:
        return self._figsize

    def set_size_inches(
        self, width: float, height: float | None = None, forward: bool = True
    ) -> None:
        if height is None:
            try:
                width, height = width  # type: ignore[misc]
            except (TypeError, ValueError):
                raise TypeError("set_size_inches expects (width, height)") from None
        self._figsize = (float(width), float(height))
        if forward and self.canvas is not None:
            self.canvas.resize(*self.get_size_pixels())

    def get_dpi(self) -> float:
        return self._dpi

    def set_dpi(self, value: float) -> None:
        self._dpi = float(value)
        if self.canvas is not None:
            self.canvas.resize(*self.get_size_pixels())

    @property
    def size_inches(self) -> tuple[float, float]:
        return self._figsize

    @property
    def dpi(self) -> float:
        return self._dpi

    def get_size_pixels(self) -> tuple[int, int]:
        return (round(self._figsize[0] * self._dpi), round(self._figsize[1] * self._dpi))

    # ---------------------------------------------------------- no-op layout
    def tight_layout(self, *args: object, **kwargs: object) -> None:
        """No-op: the one view fills its widget; there are no margins to pack."""

    def subplots_adjust(self, *args: object, **kwargs: object) -> None:
        """No-op: there is no page layout to adjust."""

    def __repr__(self) -> str:
        return f"<qmlmathplot.Figure number={self.number}>"
