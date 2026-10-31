"""Unit tests for the GGB path state machine, driven by synthetic topologies.

No GPU, no interval arithmetic: the state machine consumes exactly what ``FI`` produces
(EMPTY / CONNECTED / INVERTED / WHOLE) and these tests pin every branch of GGB's
``IntervalPath`` / ``DrawInterval`` / ``DrawInvertedInterval`` / ``JoinLines``.
"""

from __future__ import annotations

import pytest

from qmlmathplot.ggb_path import IINF, Column, IntervalPath, IVal

# A 40x40 screen over x in [0, 4], y in [0, 4]: sx(x) = 10x, sy(y) = 40 - 10y.
W = H = 40.0
X0, X1 = 0.0, 4.0
Y0, Y1 = 0.0, 4.0


def _path(columns: list[Column], x_hi: float = X1) -> list[tuple[tuple[float, float],
                                                                  tuple[float, float]]]:
    path = IntervalPath(columns, W, H, X0, x_hi, Y0, Y1)
    return [((a.x, a.y), (b.x, b.y)) for a, b in path.update()]


def _col(index: int, y: IVal) -> Column:
    return Column(float(index), float(index + 1), y)


def test_connected_columns_form_one_continuous_path() -> None:
    columns = [
        _col(0, IVal(0.0, 1.0)),
        _col(1, IVal(1.0, 2.0)),
        _col(2, IVal(2.0, 3.0)),
    ]
    segments = _path(columns)
    assert segments
    # The first column has no left neighbour: GGB's drawIndependent draws a vertical at
    # x.high (world [0, 1] -> screen [30, 40]).
    assert segments[0] == ((10.0, 30.0), (10.0, 40.0))
    # Every following segment starts where the previous one ended: one path, no jump.
    for (_, previous_end), (next_start, _) in zip(segments, segments[1:]):
        assert next_start == pytest.approx(previous_end), (
            f"the path breaks between {previous_end} and {next_start}")


def test_a_gap_breaks_the_path_and_restarts_independently() -> None:
    columns = [
        _col(0, IVal(0.0, 1.0)),
        _col(1, IVal(0.0, 0.0, "empty")),
        _col(2, IVal(1.0, 2.0)),
    ]
    segments = _path(columns)
    # Nothing may connect across the empty column (screen x 10..20).
    for (ax, ay), (bx, by) in segments:
        assert not (min(ax, bx) <= 10.0 and max(ax, bx) >= 20.0), (
            f"a segment crosses the gap: {((ax, ay), (bx, by))}")
    # The column after the gap restarts with an independent vertical at its right edge.
    after = [s for s in segments if s[0][0] == 30.0 and s[1][0] == 30.0]
    assert after, "the post-gap column should restart with a vertical at x.high"


def test_inverted_gap_within_the_view_draws_two_rays() -> None:
    columns = [_col(0, IVal(-0.5, 0.5, "inverted"))]
    segments = _path(columns)
    # Top ray: at x.high, from the viewport top to the gap's high edge (world 0.5 -> 35 px).
    assert ((10.0, 0.0), (10.0, 35.0)) in [tuple(map(tuple, s)) for s in segments]
    # Bottom ray: from the viewport bottom to the gap's low edge (world -0.5 -> 45 px).
    assert ((0.0, 45.0), (10.0, 40.0)) in [tuple(map(tuple, s)) for s in segments]


def test_a_pole_gap_does_not_draw_a_visible_connector() -> None:
    """The reason ``tan`` has no wall at its poles: the gap rays end at the *neighbours'*
    near edges, which are off screen, so the clipped segment never crosses the view."""
    columns = [
        _col(0, IVal(9.9, 10.0)),
        _col(1, IVal(-10.0, 10.0, "inverted")),
        _col(2, IVal(-10.0, -9.9)),
    ]
    segments = _path(columns)
    for (ax, ay), (bx, by) in segments:
        # A visible connector would have one endpoint at or above the top and one at or
        # below the bottom, inside the pole column's screen span (x 20..30).
        spans = min(ay, by) <= 0.0 and max(ay, by) >= H
        if spans:
            assert max(ax, bx) < 20.0 or min(ax, bx) > 30.0, (
                f"the pole column draws a full-height connector: {((ax, ay), (bx, by))}")


def test_whole_column_is_one_vertical_and_resets_the_join() -> None:
    columns = [
        _col(0, IVal(0.0, 1.0)),
        _col(1, IVal(-IINF, IINF, "whole")),
        _col(2, IVal(0.0, 1.0)),
    ]
    segments = _path(columns)
    wholes = [s for s in segments if s == ((10.0, 0.0), (10.0, 40.0))]
    assert wholes, "the whole column must be one full-height vertical at x.low"
    # The next column cannot join through the whole column: it restarts independently.
    after = [s for s in segments if s[0][0] == 30.0 and s[1][0] == 30.0]
    assert after, "the column after a whole column must restart independently"


def test_connected_infinity_reaches_the_top_edge_at_half_a_column() -> None:
    columns = [
        _col(0, IVal(0.0, 1.0)),
        _col(1, IVal(0.0, IINF)),
    ]
    segments = _path(columns)
    to_top = [s for s in segments if s[1] == pytest.approx((15.0, 0.0))]
    assert to_top, f"leftToTop should end at x.middle on the top edge: {segments}"
    # The finite bound is drawn from, not the viewport edge at the column's left.
    assert to_top[0][0] == pytest.approx((10.0, 40.0))


def test_consecutive_inverted_columns_use_plain_gap_segments() -> None:
    columns = [
        _col(0, IVal(-0.5, 0.5, "inverted")),
        _col(1, IVal(-0.5, 0.5, "inverted")),
    ]
    segments = _path(columns)
    # The first column has an inverted right neighbour: DrawInvertedInterval.drawSegments.
    assert ((10.0, 0.0), (10.0, 35.0)) in [tuple(map(tuple, s)) for s in segments]
    # The bottom ray runs from the bottom edge to the gap's low edge on the right.
    assert ((0.0, 40.0), (10.0, 45.0)) in [tuple(map(tuple, s)) for s in segments]


def test_top_edge_equality_uses_a_tolerance() -> None:
    """GGB compares the gap's high edge to the left neighbour's with MAX_PRECISION.

    The two values come from overlapping interval evaluations, so they differ in the last
    bits (2.3e-12 in GGB's own tan output). Exact equality missed the branch and drew a
    two-column diagonal instead of the vertical at x.low — the visible asymptote break.
    """
    columns = [
        _col(0, IVal(9.0, 10.0 + 2.3e-12)),
        _col(1, IVal(-15.0, 10.0, "inverted")),
        _col(2, IVal(-15.1, -4.2)),
    ]
    segments = _path(columns)
    # toTopCurrentXLow: a vertical at x.low from the left neighbour's high to the top.
    assert any(
        a[0] == pytest.approx(10.0) and b[0] == pytest.approx(10.0)
        and b[1] == pytest.approx(0.0)
        for a, b in segments)
    # And no diagonal that starts at the neighbour's right edge.
    assert not any(a[0] == pytest.approx(30.0) and a[1] == pytest.approx(0.0)
                   and b[0] == pytest.approx(10.0) for a, b in segments)


def test_bottom_branch_compares_screen_differences_like_ggb() -> None:
    """``JoinLines.toBottom`` maps the world differences through ``toScreenCoordYd``.

    That mapping is decreasing, so a smaller world difference compares *greater*. The tan
    pole case (left 18.7, right 0.04) therefore takes ``toBottomLeft`` — from the right
    neighbour's high x to the current column's high x — which is what GGB's own plotter
    emits. Raw world differences selected the mirrored branch.
    """
    columns = [
        _col(0, IVal(3.0, 9.0)),
        _col(1, IVal(-15.0, 10.0, "inverted")),
        _col(2, IVal(-15.1, -4.2)),
    ]
    segments = _path(columns)
    # (right.x.high, current.low) -> (current.x.high, y_min)
    assert ((30.0, 190.0), (20.0, 40.0)) in [tuple(map(tuple, s)) for s in segments]
