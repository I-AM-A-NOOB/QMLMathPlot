"""GeoGebra's interval-path eligibility, not a dependency heuristic.

GGB's ``IntervalFunctionSupport`` accepts a ``GeoFunction`` only when its expression
contains **one occurrence of the function variable** and every operation is supported by
the interval converter. ``MultipleVariableChecker`` is the important gate: ``x/(1+x**2)``,
``x*tan(x)`` and ``sin(1/x) + x`` are routed to GGB's sampled ``CurvePlotter`` even though
their operations are individually supported. The dependency problem is avoided by this
gate, not by trying to recognise quotients.
"""
from __future__ import annotations

import sympy as sp

__all__ = ["supported", "variable_count"]

X = sp.Symbol("x")

_FUNCTIONS: list[type[sp.Function]] = [
    sp.sin, sp.cos, sp.tan, sp.asin, sp.acos, sp.atan,
    sp.sinh, sp.cosh, sp.tanh, sp.exp, sp.log, sp.Abs,
]
for _name in ("cot", "sec", "csc"):
    _function = getattr(sp, _name, None)
    if _function is not None:
        _FUNCTIONS.append(_function)
_FUNCTIONS = tuple(_FUNCTIONS)


def variable_count(expr: sp.Expr) -> int:
    """Occurrences of x in the expression tree, matching GGB's FunctionVariable walk."""
    return sum(1 for node in sp.preorder_traversal(expr) if node == X)


def _allowed(expr: sp.Expr) -> bool:
    if expression_variables(expr):
        return False
    return _operation_allowed(expr)


def expression_variables(expr: sp.Expr) -> set[sp.Symbol]:
    return {symbol for symbol in expr.free_symbols if symbol != X}


def _operation_allowed(expr: sp.Expr) -> bool:
    if isinstance(expr, sp.Symbol):
        return expr == X
    if expr.is_Number:
        return not expr.has(sp.I) and expr.is_finite is not False
    if isinstance(expr, sp.Add | sp.Mul):
        return all(_operation_allowed(arg) for arg in expr.args)
    if isinstance(expr, sp.Pow):
        # e^f is GGB's EXP; an ordinary power needs a numeric exponent.
        if expr.base == sp.E:
            return _operation_allowed(expr.exp)
        if not expr.exp.is_number or expr.exp.has(sp.I):
            return False
        return _operation_allowed(expr.base)
    if isinstance(expr, sp.Function):
        if not isinstance(expr, _FUNCTIONS):
            return False
        return all(_operation_allowed(arg) for arg in expr.args)
    return False


def supported(expr: sp.Expr) -> bool:
    """Whether GGB would use the interval path (``IntervalFunctionSupport.isSupported``)."""
    if variable_count(expr) > 1:
        return False
    return _allowed(expr)
