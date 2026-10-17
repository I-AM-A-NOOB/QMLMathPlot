"""The one eased-camera animation shared by wheel and keyboard moves.

Kept out of :class:`~qmlmathplot.camera.Camera` so the camera stays geometry + ticks: this
object owns the ``QVariantAnimation`` and the interpolation policy, and calls back with the
state to apply. Direct manipulation (drag, pinch, direct setters) stops it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QVariantAnimation

__all__ = ["CameraAnimator"]

State = tuple[float, float, float, float]      # centre x, centre y, scale x, scale y


class CameraAnimator(QObject):
    """Interpolate between two camera states; ``apply`` runs on every frame.

    Scales interpolate geometrically (constant perceived zoom rate) and the centre linearly;
    OutCubic gives the same short settle for a wheel notch and a keypress.
    """

    def __init__(self, apply: Callable[[State], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._apply: Callable[[State], None] = apply
        self._from: State | None = None
        self._to: State | None = None
        self._animation: QVariantAnimation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_tick)
        self._animation.finished.connect(self._on_finished)

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        return self._animation.state() == QAbstractAnimation.State.Running

    def target(self, current: State) -> State:
        """The state an in-flight animation is heading for (accumulate rapid wheel/key
        presses); ``current`` otherwise."""
        if self._to is not None and self.running:
            return self._to
        return current

    # ------------------------------------------------------------- lifecycle
    def start(self, start: State, end: State, duration: int) -> None:
        duration = int(duration)
        if duration <= 0:
            self.stop()
            self._apply(end)
            return
        if start == end:
            self.stop()
            return
        self._from = start
        self._to = end
        self._animation.stop()
        self._animation.setDuration(duration)
        self._animation.start()

    def stop(self) -> None:
        if self._animation.state() != QAbstractAnimation.State.Stopped:
            self._animation.stop()
        self._from = None
        self._to = None

    # ------------------------------------------------------------- callbacks
    def _on_tick(self, value: float) -> None:
        if self._from is None or self._to is None:
            return
        t = float(value)
        fx, fy, fsx, fsy = self._from
        tx, ty, tsx, tsy = self._to
        sx = fsx * (tsx / fsx) ** t
        sy = fsy * (tsy / fsy) ** t
        self._apply((fx + (tx - fx) * t, fy + (ty - fy) * t, sx, sy))

    def _on_finished(self) -> None:
        if self._to is not None:
            self._apply(self._to)
        self._from = None
        self._to = None
