"""The synchronous ProgramSource contract and the program entry points."""

from __future__ import annotations

import pytest

from qmlmathplot import Plot, Program, ProgramSourceMissingError
from qmlmathplot.calc import SymPyProgramSource
from qmlmathplot.curve import Curve
from qmlmathplot.protocol import Program as ProgramType


def test_a_plot_without_a_source_reports_why_it_cannot_parse() -> None:
    plot = Plot(program_source=None)
    with pytest.raises(ProgramSourceMissingError, match="ProgramSource"):
        plot.add_curve("sin(x)")


def test_a_synchronous_source_returns_the_program_immediately() -> None:
    plot = Plot(program_source=SymPyProgramSource())
    curve = plot.add_curve("sin(x)")
    assert isinstance(curve.program, ProgramType)
    assert curve.program.kind == "intervals"
    assert curve.expression == "sin(x)"
    assert curve.error == ""


def test_a_program_can_be_injected_without_a_source() -> None:
    plot = Plot(program_source=None)
    program = Program(
        text="sin(x)", kind="adaptive",
        math="def f(x):\n    return math.sin(x)\n",
    )
    curve = plot.add_program(program)
    assert curve.program is program
    assert curve.expression == "sin(x)"
    assert curve.error == ""


def test_set_program_replaces_the_drawing_without_parsing() -> None:
    curve = Curve("sin(x)")
    program = Program(
        text="cos(x)", kind="adaptive",
        math="def f(x):\n    return math.cos(x)\n",
    )
    curve.set_program(program)
    assert curve.program is program and curve.expression == "cos(x)"


def test_a_program_source_can_be_swapped_after_construction() -> None:
    plot = Plot(program_source=None)
    with pytest.raises(ProgramSourceMissingError):
        plot.add_curve("sin(x)")
    plot.program_source = SymPyProgramSource()
    assert plot.add_curve("sin(x)").program.kind == "intervals"


def test_the_plot_aggregates_curve_errors() -> None:
    plot = Plot(program_source=SymPyProgramSource())
    assert plot.plotError == ""
    curve = plot.add_curve("a*sin(x)")          # parameter, not a function of x
    assert curve.error
    assert plot.plotError == curve.error


def test_points_never_need_a_source() -> None:
    plot = Plot(program_source=None)
    curve = plot.add_points([0, 1], [0, 1])
    assert curve.is_data and curve.program is not None
