"""Phase 0: the GGB oracle fixtures are complete and use the production mapping."""
from __future__ import annotations

import math

import ggb_oracle


def _supported() -> dict[str, bool]:
    out = {}
    for name in ggb_oracle.names():
        data = ggb_oracle.load(name)
        if data.get("error"):
            continue
        out[data["id"]] = data["supported"]
    return out


def test_the_fixture_set_covers_routing_and_the_problem_expression() -> None:
    names = set(ggb_oracle.names())
    assert {"mixed_default", "mixed_wide", "tan_default", "floor", "dependent"} <= names
    supported = _supported()
    # GGB's interval path is gated by MultipleVariableChecker: exactly one x occurrence.
    assert supported["tan_default"] is True
    assert supported["rational_sq"] is True   # 1/(1+x^2)^2: one x
    assert supported["rational"] is False     # x/(1+x^2): two x
    assert supported["mixed_default"] is False
    assert supported["mixed_wide"] is False
    assert supported["dependent"] is False
    assert supported["floor"] is False
    assert supported["sign"] is False
    assert supported["abs_quotient"] is False


def test_every_supported_fixture_has_columns_and_segments() -> None:
    for name in ggb_oracle.names():
        data = ggb_oracle.load(name)
        if data.get("error") or not data["supported"]:
            continue
        assert data["columns"], name
        assert data["segments"], name
        kinds = {column["kind"] for column in data["columns"]}
        assert kinds <= {"EMPTY", "CONNECTED", "INVERTED", "WHOLE", "OVERFLOW"}, name


def test_the_screen_mapping_is_not_identity() -> None:
    data = ggb_oracle.load("tan_wide_y")
    assert data["supported"] is True
    assert data["size"] == {"width": 900, "height": 600}
    assert data["segments"]
    xs = [entry["x"] for entry in data["segments"] if "x" in entry]
    assert xs
    assert all(-1e6 <= x <= 1e6 for x in xs)
    screen_y = [entry["y"] for entry in data["segments"] if "y" in entry]
    assert any(0 <= y <= 600 for y in screen_y)
    assert all(math.isfinite(y) for y in screen_y)


def test_the_problem_expression_is_not_an_interval_function() -> None:
    """GGB's MultipleVariableChecker rejects x*tan(x) and ln(cos(x)^(-2)) together."""
    data = ggb_oracle.load("mixed_wide")
    assert data["supported"] is False
    assert data["columns"] == []
    assert data["segments"] == []


def test_python_routing_matches_ggb_on_the_corpus() -> None:
    from qmlmathplot.ggb_eligibility import supported as ggb_supported
    from qmlmathplot.interval import compute_supported

    for name in ggb_oracle.names():
        data = ggb_oracle.load(name)
        if data.get("error"):
            continue
        expr = ggb_oracle.sympy_expr(data["expression"])
        assert ggb_supported(expr) is data["supported"], (name, data["expression"])
        assert compute_supported(expr) is data["supported"], (name, data["expression"])
