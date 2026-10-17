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
    curve = Curve("sin(x)")                 # construction is always synchronous
    old_fragment = curve.fragmentShader
    curve.expression = "cos(x)"
    assert curve.expression == "cos(x)"
    assert curve.fragmentShader == old_fragment, "the setter blocked on the bake"

    _wait(curve.shadersChanged)
    assert curve.fragmentShader != old_fragment
    assert curve.expr == sp.cos(X)
    assert curve.error == ""


def test_a_stale_bake_does_not_replace_the_newer_expression(app: QApplication, monkeypatch) -> None:
    monkeypatch.setattr(Curve, "bake_async", True)
    curve = Curve("sin(x)")
    curve.expression = "cos(x)"
    curve.expression = "tan(x)"             # supersedes the cos bake before it lands
    _wait(curve.shadersChanged)
    assert curve.expr == sp.tan(X)
    assert curve.expression == "tan(x)"


def test_a_failed_async_bake_keeps_the_drawn_curve(app: QApplication, monkeypatch) -> None:
    monkeypatch.setattr(Curve, "bake_async", True)
    curve = Curve("sin(x)")
    old_fragment = curve.fragmentShader
    old_expr = curve.expr

    def broken_bake(*args: object, **kwargs: object):
        raise RuntimeError("qsb exploded")

    monkeypatch.setattr(curve_module, "bake", broken_bake)
    curve.expression = "cos(x)"
    _wait(curve.errorChanged)
    assert "qsb exploded" in curve.error
    assert curve.fragmentShader == old_fragment
    assert curve.expr == old_expr
