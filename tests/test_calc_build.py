"""Program construction: routing, emitted sources, and error reporting."""

from __future__ import annotations

import math

import numpy as np
import pytest

from qmlmathplot.calc import build_program
from qmlmathplot.protocol import Program


def _call(program: Program, *values: float, numpy: bool = False) -> float:
    source = program.numpy if numpy else program.math
    assert source is not None
    module = {"numpy": np} if numpy else {"math": math}
    namespace: dict[str, object] = {**module, "abs": abs}
    exec(compile(source, "<program>", "exec"), namespace)  # noqa: S102 - our own source
    function = namespace[program.name]
    assert callable(function)
    return float(function(*values))


@pytest.mark.parametrize(
    ("text", "kind", "gpu"),
    [
        ("sin(x)", "intervals", "compute"),
        ("1/x", "intervals", "compute"),
        ("tan(2*x)", "intervals", "compute"),
        ("x*sin(x)", "adaptive", None),
        ("x/(1+x**2)", "adaptive", None),
        ("x/sqrt(x**2)", "adaptive", None),
        ("floor(x)+1", "adaptive", None),
        ("x**2 + y**2 - 1", "implicit", "fragment"),
        ("Eq(x, log(y))", "implicit", "fragment"),
    ],
)
def test_routing_matches_the_renderer_split(text: str, kind: str, gpu: str | None) -> None:
    program = build_program(text)
    assert program.error == ""
    assert program.kind == kind
    assert program.text == text
    if gpu is None:
        assert program.glsl == {}
    else:
        assert set(program.glsl) == {gpu}
        assert program.glsl[gpu]


def test_a_level_set_takes_two_arguments() -> None:
    program = build_program("x**2 + y**2 - 1")
    assert program.args == ("x", "y") and program.name == "F"
    inside = _call(program, 0.5, 0.5)
    assert inside < 0.0
    assert _call(program, 0.5, 0.5, numpy=True) == pytest.approx(inside)


def test_forcing_implicit_routes_a_one_variable_expression_to_a_level_set() -> None:
    program = build_program("x**2 - 1", implicit=True)
    assert program.kind == "implicit" and program.args == ("x", "y")
    assert program.glsl.get("fragment")


def test_math_and_numpy_programs_agree() -> None:
    for text in ("sin(x)", "x*sin(x)", "exp(-x*x)*sin(10*x)", "log(x+1)"):
        program = build_program(text)
        assert program.math is not None and program.numpy is not None
        assert "math." in program.math and "numpy." in program.numpy
        for x in (0.3, 1.2, 4.0):
            assert _call(program, x) == pytest.approx(_call(program, x, numpy=True), rel=1e-12)


def test_the_numpy_program_is_vectorised() -> None:
    program = build_program("sin(x)")
    assert program.numpy is not None
    namespace = {"numpy": np}
    exec(compile(program.numpy, "<p>", "exec"), namespace)  # noqa: S102
    values = namespace["f"](np.linspace(0.0, 1.0, 5))
    assert np.allclose(values, np.sin(np.linspace(0.0, 1.0, 5)))


def test_a_third_symbol_is_an_error() -> None:
    program = build_program("a*x")
    assert "substitute parameters first" in program.error


def test_unparsable_text_is_an_error_program() -> None:
    program = build_program("sin(")
    assert program.error
    assert program.glsl == {} and program.numpy is None and program.math is None


def test_an_equation_is_normalised_and_kept_implicit() -> None:
    program = build_program("Eq(x**2 + y**2, 1)")
    assert program.kind == "implicit"
    assert _call(program, 0.0, 1.0) == pytest.approx(0.0)
