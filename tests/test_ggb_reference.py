"""The CPU reference port of GGB's interval path."""

from __future__ import annotations

from qmlmathplot.ggb_reference import IntervalPathReference, IntervalSampler

WIDTH, HEIGHT = 120, 60


def _path(expression: str):
    columns = IntervalSampler(expression, -6.0, 6.0, WIDTH).columns()
    return columns, IntervalPathReference(
        columns, WIDTH, HEIGHT, -6.0, 6.0, -2.0, 2.0).update()


def test_reference_tan_does_not_bridge_its_pole() -> None:
    _, segments = _path("tan(x)")
    assert segments
    full = [
        (a, b) for a, b in segments
        if abs(a.x - b.x) < 0.01
        and ((a.y <= 1.0 and b.y >= HEIGHT - 1.0)
             or (b.y <= 1.0 and a.y >= HEIGHT - 1.0))
    ]
    assert not full, f"tan pole is bridged: {full}"


def test_reference_reciprocal_keeps_both_branches() -> None:
    _, segments = _path("1/(x-1)+1")
    assert len(segments) > 100
    # Segments must exist on both sides of the pole column (screen x near 70).
    left = [s for s in segments if s[0].x < WIDTH * 0.58]
    right = [s for s in segments if s[0].x > WIDTH * 0.58]
    assert left and right


def test_reference_log_has_no_path_left_of_its_domain() -> None:
    columns, segments = _path("log(x)")
    assert columns[0].y.empty
    assert segments
    assert min(a.x for a, _ in segments) >= WIDTH / 2 - 1
