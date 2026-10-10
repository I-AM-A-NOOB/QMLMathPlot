"""Curve layer unit tests: expression -> shaders, style, and the list model QML repeats over.

No GUI: a curve is a plain QObject and the shaders are baked (cached) by ``qsb``.
"""

from __future__ import annotations

import pytest
import sympy as sp
from PySide6.QtCore import QModelIndex
from PySide6.QtGui import QColor

from qmlmathplot import Curve, CurveListModel


def test_curve_bakes_its_shaders() -> None:
    curve = Curve("sin(x)")
    assert curve.error == ""
    assert curve.fragmentShader.toString().endswith(".qsb")
    assert curve.vertexShader.toString().endswith(".qsb")
    assert "sin(" not in curve.fragmentShader.toString()      # a URL, not source


def test_a_bad_expression_keeps_the_last_working_shaders() -> None:
    curve = Curve("sin(x)")
    vertex, fragment = curve.vertexShader, curve.fragmentShader
    curve.expression = "sin("                                 # cannot be parsed
    assert curve.expression == "sin("                         # the box shows what was typed
    assert curve.error, "a failed expression must report a reason"
    assert (curve.vertexShader, curve.fragmentShader) == (vertex, fragment), \
        "the previous shaders must survive a failed bake"

    curve.expression = "cos(x)"
    assert curve.error == ""
    assert curve.fragmentShader != fragment


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


def test_expression_accepts_a_string_and_a_sympy_expression(monkeypatch) -> None:
    """The parse happens once, at assignment: an ``Expr`` is never stringified and re-parsed,
    the canonical string is what identifies the expression, and a real change still re-bakes."""
    calls: list[object] = []
    real = sp.sympify

    def counting(value, *args, **kwargs):
        calls.append(value)
        return real(value, *args, **kwargs)

    monkeypatch.setattr(sp, "sympify", counting)
    x = sp.Symbol("x")

    curve = Curve(sp.sin(x))                          # an expression: no parse at all
    assert calls == [], "an Expr input must not be stringified and re-parsed"
    assert curve.expression == "sin(x)" and curve.expr == sp.sin(x)

    curve.expression = "cos(x)"                       # a string: exactly one parse
    assert calls == ["cos(x)"]
    after_string = curve.fragmentShader

    curve.expression = sp.cos(x)                      # the same expression, as an object
    assert calls == ["cos(x)"], "the canonical string already matches"
    assert curve.fragmentShader == after_string, "and nothing re-bakes"
    curve.expression = "cos(x)"                       # and a repeated string is free too
    assert calls == ["cos(x)"]

    curve.expression = sp.sin(2 * x)                  # a real change: one string conversion
    assert calls == ["cos(x)"], "still no parse"
    assert curve.expression == "sin(2*x)" and curve.fragmentShader != after_string, \
        "a real change still re-bakes"

    curve.expression = "sin("                         # unparsable: one attempt, shaders kept
    assert calls == ["cos(x)", "sin("]
    assert curve.error and curve.expression == "sin(" and curve.expr == sp.sin(2 * x)
    assert curve.fragmentShader == curve.fragmentShader

    plot = Plot()                                     # the entry point too: `add_curve(expr)`
    added = plot.add_curve(sp.tan(x))                 # must not stringify on the way in
    assert calls == ["cos(x)", "sin("], "add_curve(Expr) parsed"
    assert added.expression == "tan(x)" and added.expr == sp.tan(x)


def test_model_roles_and_data() -> None:
    model = CurveListModel()
    curve = model.add_curve("sin(1/x)", color="#ff0000", line_width=3.0, label="pole")
    assert curve.expr == sp.sin(1 / sp.Symbol("x"))   # the parsed form is cached
    names = model.roleNames()
    assert set(names.values()) == {b"curve", b"expression", b"color", b"lineWidth", b"visible",
                                   b"vertexShader", b"fragmentShader"}
    index = model.index(0, 0)
    assert model.data(index, model.CurveRole) is curve
    assert model.data(index, model.ExpressionRole) == "sin(1/x)"
    assert model.data(index, model.ColorRole) == QColor("#ff0000")
    assert model.data(index, model.LineWidthRole) == 3.0
    assert model.data(index, model.VisibleRole) is True
    assert model.data(index, model.VertexShaderRole) == curve.vertexShader
    assert model.data(index, model.FragmentShaderRole) == curve.fragmentShader
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
    # the new expression is announced first, then the freshly baked shaders
    assert report[-2:] == [[model.ExpressionRole],
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
