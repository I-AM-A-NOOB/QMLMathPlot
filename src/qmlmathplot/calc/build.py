"""Text -> :class:`Program`. The only place in the package that needs sympy at runtime.

Kept Qt-free on purpose: the same code runs in-process (:class:`qmlmathplot.client.
InlineCalculator`) and inside the worker process (``python -m qmlmathplot.calc``). The
routing rules are the model's established ones: an equation or a free ``y`` is a level set,
an expression the interval gate accepts is drawn by the compute path, everything else goes
through the CPU plotter. Shader sources and Python sources are generated here so the model
never parses an expression again.
"""

from __future__ import annotations

import sympy as sp
from sympy.printing.numpy import NumPyPrinter

from ..ggb_compute import path_compute_source
from ..interval import compute_supported
from ..model import implicit_shader_sources
from ..protocol import Program

__all__ = ["build_program", "python_sources"]

_EXTRA_SYMBOLS = "substitute parameters first"


def _parse(text: str) -> tuple[str, sp.Expr, bool]:
    """Canonical text, normalized expression, and whether the input was an equation.

    Mirrors ``qmlmathplot.curve._parse``: an ``Eq(lhs, rhs)`` becomes the level set
    ``lhs - rhs = 0`` and is always implicit.
    """
    expr = sp.sympify(text, locals={"x": sp.Symbol("x"), "y": sp.Symbol("y")})
    if not isinstance(expr, sp.Basic):
        raise ValueError(f"cannot parse {text!r}")
    if isinstance(expr, sp.Eq):
        return text, expr.lhs - expr.rhs, True
    return text, expr, False


def python_sources(
    expr: sp.Expr, args: tuple[str, ...], name: str
) -> tuple[str | None, str | None]:
    """``(math_source, numpy_source)``: complete function definitions, or ``None``.

    The two differ in the module they call (``math`` vs ``numpy``) and in vectorisation; the
    model uses ``math`` for its scalar sampler and ``numpy`` for vectorised work.
    """
    math_source = _define(expr, args, name, lambda node: sp.pycode(node))
    numpy_source = _define(expr, args, name, NumPyPrinter().doprint)
    return math_source, numpy_source


def _define(
    expr: sp.Expr,
    args: tuple[str, ...],
    name: str,
    printer: object,
) -> str | None:
    try:
        body = printer(expr)  # type: ignore[operator] - a callable passed by the caller
    except Exception:  # noqa: BLE001 - some expressions have no printer representation
        return None
    signature = ", ".join(args)
    return f"def {name}({signature}):\n    return {body}\n"


def build_program(text: str, *, implicit: bool | None = None) -> Program:
    """Turn one expression into a :class:`Program`; parse errors become ``Program.error``."""
    text = str(text)
    try:
        _, expr, is_equation = _parse(text)
    except Exception as exc:  # noqa: BLE001 - reported through the program
        return Program(text=text, error=f"{type(exc).__name__}: {exc}")
    names = {symbol.name for symbol in expr.free_symbols}
    if names - {"x", "y"}:
        return Program(
            text=text,
            error=(f"only x (or x and y) may be free, got {sorted(names - {'x', 'y'})}: "
                   f"{_EXTRA_SYMBOLS}"),
        )
    level_set = bool(implicit) or is_equation or "y" in names
    args: tuple[str, ...] = ("x", "y") if level_set else ("x",)
    kind = "implicit" if level_set else "adaptive"
    glsl: dict[str, str] = {}
    error = ""
    try:
        if level_set:
            glsl["fragment"] = implicit_shader_sources(expr)[1]
        elif compute_supported(expr):
            kind = "intervals"
            glsl["compute"] = path_compute_source(expr)
    except Exception as exc:  # noqa: BLE001 - the model keeps the last working shader
        error = f"{type(exc).__name__}: {exc}"
    math_source, numpy_source = python_sources(expr, args, "F" if level_set else "f")
    return Program(
        text=text,
        kind=kind,  # type: ignore[arg-type]
        args=args,
        glsl=glsl,
        numpy=numpy_source,
        math=math_source,
        error=error,
    )
