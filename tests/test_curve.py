"""Curve layer unit tests: expression -> shaders, style, and the list model QML repeats over.

No GUI: a curve is a plain QObject and the shaders are baked (cached) by ``qsb``.
"""

from __future__ import annotations

import pytest
import sympy as sp
from PySide6.QtCore import QModelIndex
from PySide6.QtGui import QColor

from qmlmathplot import Curve, CurveListModel


def test_an_unsupported_curve_uses_the_cpu_curve_plotter() -> None:
    """The sampled fragment fallback is gone for 1-D expressions; the CPU path owns them."""
    curve = Curve("floor(x)")
    assert curve.error == ""
    assert not curve.fragmentShader.toString()
    assert not curve.vertexShader.toString()
    assert curve.program is not None and curve.program.kind == "adaptive"
    assert curve.program.math and "math.floor" in curve.program.math


def test_a_compute_curve_skips_the_sampled_bake() -> None:
    """The compute renderer owns `expr`; baking the unused fallback was ~130 ms/curve."""
    curve = Curve("sin(x)")
    assert curve.error == ""
    assert curve.program is not None and curve.program.kind == "intervals"
    assert curve.program.glsl.get("compute")
    assert not curve.fragmentShader.toString()
    assert not curve.vertexShader.toString()


def test_a_bad_expression_keeps_the_last_working_expression() -> None:
    curve = Curve("floor(x)")
    drawn = curve.program
    curve.expression = "floor("                               # cannot be parsed
    assert curve.expression == "floor("                       # the box shows what was typed
    assert curve.error, "a failed expression must report a reason"
    assert curve.program is drawn, "the previous program survives"

    curve.expression = "floor(2*x)"
    assert curve.error == ""
    assert curve.program is not None and curve.program.text == "floor(2*x)"


def test_curve_signals_fire_on_real_changes_only() -> None:
    curve = Curve("sin(x)")
    seen = {name: 0 for name in ("expression", "style", "visible", "label")}
    curve.expressionChanged.connect(lambda: seen.__setitem__("expression", seen["expression"] + 1))
    curve.styleChanged.connect(lambda: seen.__setitem__("style", seen["style"] + 1))
    curve.visibleChanged.connect(lambda: seen.__setitem__("visible", seen["visible"] + 1))
    curve.labelChanged.connect(lambda: seen.__setitem__("label", seen["label"] + 1))

    curve.expression = "sin(x)"
    curve.visible = True
    curve.label = ""
    assert seen == {"expression": 0, "style": 0, "visible": 0, "label": 0}

    curve.expression = "tan(x)"
    curve.visible = False
    curve.label = "tan"
    assert seen == {"expression": 1, "style": 0, "visible": 1, "label": 1}

    # QML hands over whatever JS has: 1 for a bool, a string for a colour, an int for a float
    curve.visible = 1
    curve.color = "#ff0000"
    curve.lineWidth = 2
    assert curve.color == QColor("#ff0000")
    assert curve.lineWidth == 2.0
    assert seen["style"] == 2                                 # colour and width, one signal each
    curve.color = "#ff0000"
    curve.lineWidth = 2.0
    assert seen["style"] == 2                                 # …and nothing on a repeat


def test_expression_accepts_a_string_and_a_sympy_expression() -> None:
    """A sympy object is converted to its canonical text once; repeats are free."""
    x = sp.Symbol("x")

    curve = Curve(sp.floor(x))
    assert curve.expression == "floor(x)" and curve.program is not None
    assert curve.program.text == "floor(x)"

    changed = []
    curve.expressionChanged.connect(lambda: changed.append(curve.expression))
    curve.expression = "floor(2*x)"
    assert curve.program is not None and curve.program.text == "floor(2*x)"
    assert changed == ["floor(2*x)"]

    curve.expression = sp.floor(2 * x)               # the same canonical string
    assert changed == ["floor(2*x)"], "a repeated expression must not re-request"

    curve.expression = sp.floor(3 * x)
    assert curve.expression == "floor(3*x)" and curve.program is not None
    assert curve.program.text == "floor(3*x)"

    drawn = curve.program
    curve.expression = "floor("
    assert curve.error and curve.expression == "floor("
    assert curve.program is drawn, "the last working program survives"


def test_model_roles_and_data() -> None:
    model = CurveListModel()
    curve = model.add_curve("sin(1/x)", color="#ff0000", line_width=3.0, label="pole")
    assert curve.program is not None and curve.program.kind == "intervals"
    names = model.roleNames()
    assert set(names.values()) == {b"curve", b"expression", b"color", b"lineWidth", b"visible",
                                   b"vertexShader", b"fragmentShader", b"label", b"mathSource",
                                   b"implicit"}
    index = model.index(0, 0)
    assert model.data(index, model.CurveRole) is curve
    assert model.data(index, model.ExpressionRole) == "sin(1/x)"
    assert model.data(index, model.ColorRole) == QColor("#ff0000")
    assert model.data(index, model.LineWidthRole) == 3.0
    assert model.data(index, model.VisibleRole) is True
    assert model.data(index, model.VertexShaderRole) == curve.vertexShader
    assert model.data(index, model.FragmentShaderRole) == curve.fragmentShader
    assert model.data(index, model.MathSourceRole) == curve.mathSource
    assert model.data(QModelIndex(), model.CurveRole) is None


def test_model_add_remove_and_colours_from_the_cycle() -> None:
    model = CurveListModel(("#111111", "#222222"), default_line_width=2.5)
    first = model.add_curve("sin(x)")
    second = model.add_curve("cos(x)")
    third = model.add_curve("tan(x)")
    assert [first.color.name(), second.color.name(), third.color.name()] == \
        ["#111111", "#222222", "#111111"]                     # the cycle repeats
    assert first.lineWidth == 2.5                             # the model's default width
    assert third.lineWidth == 2.5
    assert len(model) == 3 and model.rowCount() == 3
    assert model.at(1) is second and model.index_of(second) == 1

    model.remove_curve(second)
    assert len(model) == 2
    assert model.at(1) is third                               # rows shift up
    assert model.index_of(second) == -1
    model.remove_curve(second)                                # removing twice is a no-op
    assert len(model) == 2
    with pytest.raises(IndexError):
        model.at(5)


def test_model_reports_changes_with_the_right_roles() -> None:
    model = CurveListModel()
    curve = model.add_curve("sin(x)")
    report = []
    model.dataChanged.connect(lambda top, bottom, roles: report.append(list(roles)))

    curve.expression = "cos(x)"
    # the new expression is announced first, then the program arrives with its math source
    # and its freshly baked shader
    assert report[-2:] == [[model.MathSourceRole, model.ImplicitRole],
                           [model.VertexShaderRole, model.FragmentShaderRole]]
    curve.shadersChanged.emit()
    assert report[-1] == [model.VertexShaderRole, model.FragmentShaderRole]
    curve.color = "#123456"
    assert report[-1] == [model.ColorRole, model.LineWidthRole]
    curve.visible = False
    assert report[-1] == [model.VisibleRole]

    # a removed curve stops reporting (the signal connection outlives the row)
    model.remove_curve(curve)
    before = len(report)
    curve.visible = True
    assert len(report) == before
