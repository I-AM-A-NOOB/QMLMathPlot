// ---- path state (one invocation walks every column, so globals are per-path) ----------
vec2 g_point;
bool g_has_point = false;
IVal g_last;
bool g_has_last = false;
IVal g_left;
IVal g_next;

float pay_lo(IVal v) { return v.kind == IK_WHOLE ? -IINF : v.lo; }
float pay_hi(IVal v) { return v.kind == IK_WHOLE ? IINF : v.hi; }
// GGB's DoubleUtil.isEqual(0, diff, MAX_PRECISION) with a float-safe relative epsilon.
bool nearly_zero(float value) {
    return abs(value) <= 1e-5 * max(1.0, abs(value));
}
// JoinLines.getLeft/RightScreenDifference: toScreenCoordYd is decreasing, so the ordering
// of a world-space comparison is reversed.
float screen_difference(float value, IVal neighbour, bool right) {
    if (neighbour.kind == IK_EMPTY) return -1.0;
    if (neighbour.kind == IK_WHOLE) return right ? 1e30 : -1e30;
    return syc(value);
}
bool infinite_boundary(IVal v) {
    return v.kind != IK_EMPTY
        && (v.kind == IK_WHOLE || v.lo <= -IINF || v.hi >= IINF);
}
// The state machine records up to two segments per column; one emission site in the
// production main then writes them. `add_segment` is the GGB segment primitive (with the
// plotter's PLOT_MARGIN clip); `line_to` is moveTo/lineTo and is not clipped here.
vec2 g_seg_a[2];
vec2 g_seg_b[2];
int g_seg_n = 0;
void add_segment(vec2 a, vec2 b) {
    if ((a.y < 1.0 && b.y < 1.0) || (a.y > size.y - 1.0 && b.y > size.y - 1.0)) {
        return;
    }
    if (g_seg_n < 2) {
        g_seg_a[g_seg_n] = a;
        g_seg_b[g_seg_n] = b;
        g_seg_n = g_seg_n + 1;
    }
    g_point = b;
    g_has_point = true;
}

void line_to(vec2 p) {
    if (g_has_point && distance(g_point, p) > 1e-6) {
        add_segment(g_point, p);
    }
    g_point = p;
    g_has_point = true;
}

void whole_column(int col) {
    add_segment(vec2(sx(col), 0.0), vec2(sx(col), size.y));
    g_has_last = false;
}

void normal_infinity(int col, IVal y) {
    if (y.lo <= -IINF && y.hi >= IINF) {
        g_has_last = false;
        return;
    }
    if (y.hi >= IINF) {
        if (ybot() <= y.lo && y.lo <= ytop()) {
            add_segment(vec2(sx(col), syc(y.lo)), vec2(smx(col), 0.0));
            g_last = i_conn(ytop(), ytop());
            g_has_last = true;
        } else {
            g_has_last = false;
        }
        return;
    }
    if (ybot() <= y.hi && y.hi <= ytop()) {
        add_segment(vec2(sx(col), syc(y.hi)), vec2(smx(col), size.y));
        g_last = i_conn(ybot(), ybot());
        g_has_last = true;
    } else {
        g_has_last = false;
    }
}

void joined_column(int col, float x0, float x1, IVal y) {
    // GGB uses the interval chord for every joined column. A dependent expression can
    // overestimate it; that is the reference behaviour (and such expressions are routed to
    // CurvePlotter by the eligibility gate).
    // GGB compares the screen high edges; screen high is the world low edge.
    bool up = y.lo < g_last.lo;
    vec2 first = up ? vec2(sx(col), syc(y.hi)) : vec2(sx(col), syc(y.lo));
    vec2 second = up ? vec2(sx(col + 1), syc(y.lo)) : vec2(sx(col + 1), syc(y.hi));
    line_to(first);
    line_to(second);
    g_last = y;
    g_has_last = true;
}

void to_top(int col, IVal gap) {
    bool has_left = g_left.kind != IK_EMPTY;
    bool has_right = g_next.kind != IK_EMPTY;
    float current = pay_hi(gap);
    float ld = has_left ? abs(current - pay_hi(g_left)) : IINF;
    float rd = has_right ? abs(current - pay_hi(g_next)) : IINF;
    float left_x = sx(col);
    float right_x = has_right ? sx(col + 2) : sx(col + 1);
    if (nearly_zero(ld) && has_left) {
        // JoinLines.toTopCurrentXLow: an off-screen high edge is not drawn.
        float y = pay_hi(g_left);
        if (y > ytop() + 5.0) return;
        add_segment(vec2(sx(col), syc(y)), vec2(sx(col), 0.0));
    } else if (nearly_zero(rd) && has_right) {
        // JoinLines.toTopCurrentXHigh: rightXHigh -> currentXHigh.
        float y = pay_hi(g_next);
        if (y > ytop() + 5.0) return;
        add_segment(vec2(right_x, syc(y)), vec2(sx(col + 1), 0.0));
    } else if (ld < rd) {
        // JoinLines.toTopLeft: rightXHigh, top -> leftXHigh, currentYHigh.
        if (current < ytop()) {
            add_segment(vec2(right_x, 0.0), vec2(left_x, syc(current)));
        }
    } else {
        // JoinLines.topToRight: rightXHigh, top -> rightXHigh, currentYHigh.
        if (current < ytop()) {
            add_segment(vec2(right_x, 0.0), vec2(right_x, syc(current)));
        }
    }
}

void to_bottom(int col, IVal gap) {
    bool has_left = g_left.kind != IK_EMPTY;
    bool has_right = g_next.kind != IK_EMPTY;
    float current = pay_lo(gap);
    float ld = has_left ? screen_difference(abs(current - pay_lo(g_left)), g_left, false) : -1.0;
    float rd = has_right ? screen_difference(abs(current - pay_lo(g_next)), g_next, true) : -1.0;
    float left_x = sx(col);
    float right_x = has_right ? sx(col + 2) : sx(col + 1);
    if (ld == 0.0) {
        add_segment(vec2(sx(col), syc(pay_lo(g_left))), vec2(sx(col), size.y));
    } else if (rd == 0.0) {
        // JoinLines.toBottomCurrentXHigh: rightXHigh -> currentXHigh.
        add_segment(vec2(right_x, syc(current)), vec2(sx(col + 1), size.y));
    } else if (ld < rd) {
        // JoinLines.toBottomLeft: to *currentXHigh*, not currentXLow.
        if (current < ybot() - 5.0) return;
        add_segment(vec2(right_x, syc(current)), vec2(sx(col + 1), size.y));
    } else {
        // JoinLines.toBottomRight: currentXLow -> currentXHigh.
        if (current < ybot() - 5.0) return;
        add_segment(vec2(sx(col), syc(current)), vec2(sx(col + 1), size.y));
    }
}

void inverted_segments(int col, IVal gap) {
    // DrawInvertedInterval.drawSegments: the gap is excluded, so two rays.
    if (gap.hi > ybot()) {
        add_segment(vec2(sx(col), size.y), vec2(sx(col + 1), syc(gap.lo)));
    }
    if (gap.lo < ytop()) {
        add_segment(vec2(sx(col + 1), 0.0), vec2(sx(col + 1), syc(gap.hi)));
    }
}

void inverted_joined(int col, IVal gap) {
    // JoinLines.inverted: the rays end at the neighbours' near edges, not the viewport
    // edges. When those neighbours are next to a pole their edges are off screen, which is
    // exactly why the clipped path does not cross the view.
    if (gap.hi < IINF) {
        if (!infinite_boundary(g_left)) {
            to_top(col, gap);
        }
    }
    if (gap.lo > -IINF) {
        if (infinite_boundary(g_next)) {
            float y1 = g_left.kind != IK_EMPTY ? pay_lo(g_left) : gap.lo;
            add_segment(vec2(sx(col), syc(y1)), vec2(sx(col), size.y));
        } else {
            to_bottom(col, gap);
        }
    }
}

void inverted_column(int col, IVal gap) {
    if (g_next.kind == IK_INVERTED) {
        inverted_segments(col, gap);
    } else {
        inverted_joined(col, gap);
        // DrawInvertedInterval.draw() clears lastYSet when the next column is not inverted;
        // without this the stale lastY joins the two branches across the pole.
        g_has_last = false;
    }
}

void independent_column(int col, IVal y) {
    if (y.kind == IK_WHOLE) {
        whole_column(col);
        return;
    }
    if (y.kind == IK_INVERTED) {
        inverted_column(col, y);
        return;
    }
    // DrawInterval.drawIndependent -> line(x, y): moveTo + lineTo, a new subpath.
    float x = sx(col + 1);
    g_has_point = false;
    line_to(vec2(x, syc(y.hi)));
    line_to(vec2(x, syc(y.lo)));
    g_last = y;
    g_has_last = true;
}

void draw_column(int col, float x0, float x1, IVal cur) {
    if (!g_has_last) {
        independent_column(col, cur);
    } else if (cur.kind == IK_WHOLE) {
        whole_column(col);
    } else if (cur.kind == IK_INVERTED) {
        inverted_column(col, cur);
    } else if (cur.lo <= -IINF || cur.hi >= IINF) {
        normal_infinity(col, cur);
    } else {
        joined_column(col, x0, x1, cur);
    }
}

