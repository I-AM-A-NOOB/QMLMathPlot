"""P1a: the current interval semantics against GGB's per-column oracle.

The reference mirrors ``interval_lib.glsl``. Exact fixtures must match; the known gaps are
xfail markers for the P1b fixes (GGB's IntervalDivide topology, the log boundary and
overflow).
"""
from __future__ import annotations

import math

import ggb_oracle
import pytest

from qmlmathplot.calc.interval_reference import (
    CONNECTED,
    EMPTY,
    INVERTED,
    OVERFLOW,
    WHOLE,
    evaluate,
)

REVERSE = {EMPTY: "EMPTY", CONNECTED: "CONNECTED", INVERTED: "INVERTED", WHOLE: "WHOLE", OVERFLOW: "OVERFLOW"}
EXACT = [
    "cube_root", "rational_sq", "sin_inv_default", "tan_default", "tan_wide_y",
    "tan_double", "inv_default", "ln_default", "exp_tan_wide",
]
KNOWN = ["cot_sec", "ln_cosh", "sqrt_tan_cot"]


def _num(value):
    if isinstance(value, (int, float)):
        return float(value)
    return {"Infinity": math.inf, "-Infinity": -math.inf, "NaN": math.nan}[value]


def _matches(got, column) -> bool:
    # GGB's FunctionSampler reports an OVERFLOW y as EMPTY; both break the path.
    got_kind = REVERSE[got.kind]
    if got_kind == "OVERFLOW" and column["kind"] == "EMPTY":
        return True
    if got_kind != column["kind"]:
        return False
    if column["kind"] not in ("CONNECTED", "INVERTED"):
        return True
    for value, key in ((got.lo, "lo"), (got.hi, "hi")):
        want = _num(column[key])
        if math.isinf(want) or abs(want) >= 1e29:
            if not (math.isinf(value) or abs(value) >= 1e29):
                return False
        elif not math.isclose(value, want, rel_tol=1e-9, abs_tol=1e-6):
            return False
    return True


def _mismatches(name: str) -> int:
    data = ggb_oracle.load(name)
    expression = ggb_oracle.sympy_expr(data["expression"])
    return sum(
        not _matches(evaluate(expression, column["x"][0], column["x"][1]), column)
        for column in data["columns"]
    )


@pytest.mark.parametrize("name", EXACT)
def test_the_current_interval_topology_matches_ggb(name: str) -> None:
    assert _mismatches(name) == 0, name


@pytest.mark.parametrize("name", KNOWN)
@pytest.mark.xfail(strict=False, reason="P1b: GGB IntervalDivide/log/overflow semantics")
def test_the_known_interval_gaps_are_explicit(name: str) -> None:
    assert _mismatches(name) == 0, name
