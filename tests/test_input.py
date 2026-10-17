"""Keyboard, touch and animated camera moves (real window, real events).

The keyboard belongs to the view: a click gives the QML root active focus, the arrow keys pan
through the camera, and the shortcuts are properties on the camera so a host can rebind them.
Wheel/keyboard moves are eased; drags and touch gestures are direct manipulation.

Touch events are sent to the ``QQuickWidget``'s offscreen ``QQuickWindow`` (the QWidget does
not translate them). PySide6 does not export the nested ``QTouchEventSequence`` type at module
level, so the sequence's own methods cannot resolve their arguments without the alias below.
"""

from __future__ import annotations

import PySide6.QtTest as _qt_test
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication

from qmlmathplot import MathPlotWidget

pytestmark = pytest.mark.gui

_qt_test.QTouchEventSequence = QTest.QTouchEventSequence


def _quick(plot: MathPlotWidget) -> QQuickWidget:
    quick = plot.findChild(QQuickWidget)
    assert quick is not None
    return quick


def _shown_plot() -> MathPlotWidget:
    plot = MathPlotWidget("sin(x)")
    plot.resize(600, 400)
    plot.show()
    assert QTest.qWaitForWindowExposed(plot), "no usable display/GPU scenegraph"
    QTest.qWait(50)
    return plot


def _focus(plot: MathPlotWidget) -> QQuickWidget:
    quick = _quick(plot)
    QTest.mouseClick(quick, Qt.LeftButton, pos=QPoint(quick.width() // 2, quick.height() // 2))
    QTest.qWait(20)
    assert quick.rootObject().property("activeFocus"), "the click did not focus the view"
    return quick


def _wait_until_still(plot: MathPlotWidget, timeout_ms: int = 1500) -> tuple[float, ...]:
    """Wait until three consecutive samples report the same view (the animation ended).

    A fixed ``qWait`` is flaky: under a loaded machine the animation's last frame can land
    after the nominal duration, and the exact target is only written when it finishes.
    """
    last = plot.view_bounds()
    stable = 0
    for _ in range(timeout_ms // 20):
        QTest.qWait(20)
        now = plot.view_bounds()
        stable = stable + 1 if now == last else 0
        last = now
        if stable >= 3:
            break
    return last


def test_wheel_zoom_is_eased(app: QApplication) -> None:
    plot = _shown_plot()
    camera = plot.plot.camera
    camera.animationDuration = 250
    quick = _quick(plot)
    before = plot.view_bounds()
    span = before[1] - before[0]

    center = QPointF(quick.width() / 2, quick.height() / 2)
    event = QWheelEvent(
        center,
        QPointF(quick.mapToGlobal(center.toPoint())),
        QPoint(0, 0),
        QPoint(0, 120),                        # one notch, positive = closer
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(quick, event)

    QTest.qWait(80)
    mid = plot.view_bounds()
    mid_span = mid[1] - mid[0]
    assert mid_span < span, "the wheel did not start a zoom"
    assert mid_span > span * camera.zoomStep, "the zoom was not eased (it jumped to the target)"

    after = _wait_until_still(plot)
    assert after[1] - after[0] == pytest.approx(span * camera.zoomStep)
    assert (after[0] + after[1]) / 2 == pytest.approx((before[0] + before[1]) / 2)
    plot.hide()


def test_focusing_the_widget_enables_the_keys(app: QApplication) -> None:
    """``MathPlotWidget.setFocus()`` must reach the QML root: the widget uses the quick
    widget as its focus proxy, and the view is the scene's default focus item."""
    plot = _shown_plot()
    camera = plot.plot.camera
    camera.animationDuration = 0
    quick = _quick(plot)
    plot.setFocus()
    QTest.qWait(30)
    assert quick.rootObject().property("activeFocus")
    before = plot.view_bounds()
    QTest.keyClick(quick, Qt.Key_Right)
    assert plot.view_bounds()[0] > before[0]
    plot.hide()


def test_arrow_keys_animate_the_pan(app: QApplication) -> None:
    plot = _shown_plot()
    camera = plot.plot.camera
    camera.animationDuration = 250
    quick = _focus(plot)

    before = plot.view_bounds()
    step = camera.input.keyPanStep * (before[1] - before[0])   # 10% of the viewport
    QTest.keyClick(quick, Qt.Key_Left)

    QTest.qWait(80)
    mid = plot.view_bounds()
    assert mid[0] < before[0], "the key did not start a pan"
    assert mid[0] > before[0] - step - 1e-6, "the move was not eased (it jumped to the target)"

    after = _wait_until_still(plot)
    assert after[0] == pytest.approx(before[0] - step)
    assert after[1] == pytest.approx(before[1] - step)
    assert after[2:] == pytest.approx(before[2:])

    QTest.keyClick(quick, Qt.Key_Up)
    up = _wait_until_still(plot)
    y_step = camera.input.keyPanStep * (before[3] - before[2])
    assert up[2] == pytest.approx(after[2] + y_step)
    assert up[0] == pytest.approx(after[0])             # vertical only
    plot.hide()


def test_shortcuts_are_configurable(app: QApplication) -> None:
    plot = _shown_plot()
    camera = plot.plot.camera
    camera.animationDuration = 0                        # immediate, so the test needs no waits
    quick = _focus(plot)

    camera.input.panLeftShortcut = "A"
    before = plot.view_bounds()
    QTest.keyClick(quick, Qt.Key_Left)
    assert plot.view_bounds() == pytest.approx(before), "the old Left binding still fired"
    QTest.keyClick(quick, Qt.Key_A)
    assert plot.view_bounds()[0] < before[0], "the configured A binding did not pan"

    camera.input.zoomInShortcut = "Ctrl+="
    before = plot.view_bounds()
    width = before[1] - before[0]
    QTest.keyClick(quick, Qt.Key_Equal)
    assert plot.view_bounds() == pytest.approx(before), "the old zoom binding still fired"
    QTest.keyClick(quick, Qt.Key_Equal, Qt.KeyboardModifier.ControlModifier)
    after = plot.view_bounds()
    assert after[1] - after[0] == pytest.approx(width * camera.zoomStep)
    plot.hide()


def test_held_arrow_keeps_panning_without_the_os_repeat_delay(app: QApplication) -> None:
    """Holding a direction moves continuously: the view's Timer repeats, so there is no
    initial one-step + ~0.5 s pause waiting for the OS key repeat."""
    plot = _shown_plot()
    camera = plot.plot.camera
    camera.animationDuration = 100
    quick = _focus(plot)
    before = plot.view_bounds()
    step = camera.input.keyPanStep * (before[1] - before[0])

    QTest.keyPress(quick, Qt.Key_Left)
    QTest.qWait(250)                                  # ~3 timer ticks
    QTest.keyRelease(quick, Qt.Key_Left)
    held = _wait_until_still(plot)
    assert held[0] < before[0] - 1.5 * step, "holding the key did not repeat the pan"
    assert held[2:] == pytest.approx(before[2:])
    plot.hide()


def test_two_arrow_keys_pan_diagonally(app: QApplication) -> None:
    """Left+Up held together move both axes; each axis keeps the normalised step, so a
    diagonal is not sqrt(2) faster than a straight pan."""
    plot = _shown_plot()
    camera = plot.plot.camera
    camera.animationDuration = 100
    quick = _focus(plot)
    before = plot.view_bounds()
    x_step = camera.input.keyPanStep * (before[1] - before[0])
    y_step = camera.input.keyPanStep * (before[3] - before[2])

    QTest.keyPress(quick, Qt.Key_Left)
    QTest.keyPress(quick, Qt.Key_Up)
    QTest.qWait(250)
    QTest.keyRelease(quick, Qt.Key_Left)
    QTest.keyRelease(quick, Qt.Key_Up)
    after = _wait_until_still(plot)

    assert after[0] < before[0] - 1.5 * x_step
    assert after[2] > before[2] + 1.5 * y_step
    plot.hide()


def test_keyboard_and_pan_can_be_switched_off(app: QApplication) -> None:
    plot = _shown_plot()
    camera = plot.plot.camera
    camera.animationDuration = 0
    quick = _focus(plot)
    before = plot.view_bounds()

    camera.input.keyboardEnabled = False
    QTest.keyClick(quick, Qt.Key_Left)
    assert plot.view_bounds() == pytest.approx(before)
    assert camera.animationDuration == 0                # no unrelated side effect

    camera.input.keyboardEnabled = True
    camera.panEnabled = False
    QTest.keyClick(quick, Qt.Key_Left)
    assert plot.view_bounds() == pytest.approx(before)
    camera.panEnabled = True

    camera.animationDuration = 200
    camera.animate_pan(camera.input.keyPanStep * 600, 0, 100)
    QTest.qWait(30)
    assert plot.view_bounds()[0] < before[0], "animate_pan did not start"
    QTest.qWait(250)
    assert plot.view_bounds()[0] < before[0] - 1.0
    plot.hide()


def test_two_finger_pinch_scales_the_view(app: QApplication) -> None:
    plot = _shown_plot()
    window = _quick(plot).quickWindow()
    device = QTest.createTouchDevice()

    before = plot.view_bounds()
    sequence = QTest.touchEvent(window, device)
    sequence.press(0, QPoint(250, 200)).press(1, QPoint(350, 200)).commit()
    QTest.qWait(50)

    # Fingers 100 px apart -> 200 px apart: magnify by 2, anchored at the midpoint.
    sequence = QTest.touchEvent(window, device)
    sequence.move(0, QPoint(200, 200)).move(1, QPoint(400, 200)).commit()
    QTest.qWait(80)

    after = plot.view_bounds()
    assert after[1] - after[0] == pytest.approx(0.5 * (before[1] - before[0]), rel=0.02)
    assert after[3] - after[2] == pytest.approx(0.5 * (before[3] - before[2]), rel=0.02)
    assert (after[0] + after[1]) / 2 == pytest.approx((before[0] + before[1]) / 2)

    sequence = QTest.touchEvent(window, device)
    sequence.release(0, QPoint(200, 200)).release(1, QPoint(400, 200)).commit()
    QTest.qWait(50)
    plot.hide()


def test_one_finger_touch_pans(app: QApplication) -> None:
    plot = _shown_plot()
    window = _quick(plot).quickWindow()
    device = QTest.createTouchDevice()
    camera = plot.plot.camera
    before = plot.view_bounds()

    sequence = QTest.touchEvent(window, device)
    sequence.press(0, QPoint(300, 200)).commit()
    QTest.qWait(30)
    sequence = QTest.touchEvent(window, device)
    sequence.move(0, QPoint(350, 200)).commit()
    QTest.qWait(80)

    after = plot.view_bounds()
    # The content follows the finger: a 50 px rightward drag moves the camera left.
    assert after[0] == pytest.approx(before[0] - 50 * camera.scale_x)
    assert after[2:] == pytest.approx(before[2:])

    sequence = QTest.touchEvent(window, device)
    sequence.release(0, QPoint(350, 200)).commit()
    QTest.qWait(30)
    plot.hide()
