"""Matplotlib-shaped canvas: a QWidget that hosts one compatibility :class:`Figure`.

QtWidgets is imported here, so this module stays lazy in ``qmlmathplot.__init__`` exactly like
:mod:`qmlmathplot.widget`: a pure-QML app never loads QtWidgets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from .widget import MathPlotWidget

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .figure import Figure

__all__ = ["FigureCanvas", "FigureCanvasQt"]


class FigureCanvasQt(MathPlotWidget):
    """A ``MathPlotWidget`` attached to a compatibility ``Figure``.

    Matplotlib code constructs canvases and then calls ``draw()``; here the Qt scene is live,
    so ``draw()`` / ``draw_idle()`` are harmless no-ops. ``print_figure`` and ``savefig``
    forward to the same region-based export as :meth:`Figure.savefig`.
    """

    def __init__(
        self,
        figure: Figure | None = None,
        parent: QWidget | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
        dpi: float | None = None,
    ) -> None:
        from .figure import Figure

        host = figure if figure is not None else Figure()
        if dpi is not None:
            host.set_dpi(dpi)
        super().__init__(None, parent, plot=host.plot)
        self._figure = host
        host.canvas = self
        target_width, target_height = host.get_size_pixels()
        if width is not None:
            target_width = int(width)
        if height is not None:
            target_height = int(height)
        self.resize(target_width, target_height)

    @property
    def figure(self) -> "Figure":
        return self._figure

    @property
    def figure_canvas(self) -> "FigureCanvasQt":
        """Matplotlib's canvas property is called ``figure_canvas`` on some backends; this
        alias keeps both spellings working."""
        return self

    # ---------------------------------------------------------- live scene
    def draw(self) -> None:
        """No-op: the scene repaints from model signals; there is no deferred renderer."""
        self.update()

    def draw_idle(self) -> None:
        self.update()

    def flush_events(self) -> None:
        """No-op: Qt owns the event loop."""

    # -------------------------------------------------------------- export
    def print_figure(self, filename: str, **kwargs: object) -> None:
        self._figure.savefig(filename, **kwargs)

    def savefig(self, filename: str, **kwargs: object) -> None:
        self._figure.savefig(filename, **kwargs)

    # ------------------------------------------------------------- geometry
    def get_width_height(self, physical: bool = False) -> tuple[int, int]:
        if physical:
            ratio = self.devicePixelRatioF()
            return (round(self.width() * ratio), round(self.height() * ratio))
        return (self.width(), self.height())

    @property
    def device_pixel_ratio(self) -> float:
        return self.devicePixelRatioF()

    # ------------------------------------------------------ explicitly absent
    def mpl_connect(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError(
            "mpl_connect is not part of QMLMathPlot: Qt signals are the event system "
            "(camera.viewChanged, camera.aspectChanged, curve.expressionChanged, …)"
        )

    def blit(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError(
            "blit is not part of QMLMathPlot: the Qt scene is live, there is no raster "
            "buffer to restore"
        )


#: The shorter spelling used by ``matplotlib.backends.backend_qt*``.
FigureCanvas = FigureCanvasQt
