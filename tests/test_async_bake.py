"""The asynchronous bake path: qsb runs off the GUI thread, stale results are discarded, and
a failed bake keeps the last working shaders. Marked ``gui`` because only a QApplication has
the queued-signal delivery this path needs (and the rest of the suite forces synchronous bakes).
"""

from __future__ import annotations

import time

import pytest
import sympy as sp
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import qmlmathplot.curve as curve_module
from qmlmathplot import Curve

pytestmark = pytest.mark.gui

X = sp.Symbol("x")


def _wait(signal, timeout_ms: int = 8000) -> None:
    loop = QEventLoop()
    signal.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


def test_expression_bakes_off_the_gui_thread(app: QApplication, monkeypatch) -> None:
    monkeypatch.setattr(Curve, "bake_async", True)
    real_bake = curve_module.bake

    def slow_bake(source: str, stage: str, *args: object, **kwargs: object):
        time.sleep(0.15)
        return real_bake(source, stage, *args, **kwargs)

    monkeypatch.setattr(curve_module, "bake", slow_bake)
    # The async shader bake is now only for implicit curves; 1-D unsupported expressions
    # use the CPU CurvePlotter and have no shader to bake.
    curve = Curve("x**2 + y**2 - 1")          # construction is always synchronous
    old_fragment = curve.fragmentShader
    curve.expression = "x**2 + y**2 - 2"
    assert curve.expression == "x**2 + y**2 - 2"
    assert curve.fragmentShader == old_fragment, "the setter blocked on the bake"

    _wait(curve.shadersChanged)
    assert curve.fragmentShader != old_fragment
    assert curve.expr == sp.sympify("x**2 + y**2 - 2")
    assert curve.error == ""


def test_a_stale_bake_does_not_replace_the_newer_expression(app: QApplication, monkeypatch) -> None:
    monkeypatch.setattr(Curve, "bake_async", True)
    curve = Curve("x**2 + y**2 - 1")
    curve.expression = "x**2 + y**2 - 2"
    curve.expression = "x**2 + y**2 - 3"        # supersedes the -2 bake before it lands
    _wait(curve.shadersChanged)
    assert curve.expr == sp.sympify("x**2 + y**2 - 3")
    assert curve.expression == "x**2 + y**2 - 3"


def test_a_failed_async_bake_keeps_the_drawn_curve(app: QApplication, monkeypatch) -> None:
    monkeypatch.setattr(Curve, "bake_async", True)
    curve = Curve("x**2 + y**2 - 1")
    old_fragment = curve.fragmentShader
    old_expr = curve.expr

    def broken_bake(*args: object, **kwargs: object):
        raise RuntimeError("qsb exploded")

    monkeypatch.setattr(curve_module, "bake", broken_bake)
    curve.expression = "x**2 + y**2 - 2"
    _wait(curve.errorChanged)
    assert "qsb exploded" in curve.error
    assert curve.fragmentShader == old_fragment
    assert curve.expr == old_expr
