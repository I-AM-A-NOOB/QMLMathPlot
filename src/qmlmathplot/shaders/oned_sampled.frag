#version 440
layout(location = 0) in vec2 vUV;
layout(location = 0) out vec4 fragColor;
@INCLUDE:common/uniforms.glsl@


@INCLUDE:common/arg_reduction.glsl@

@INCLUDE:common/seg_dist.glsl@

@INCLUDE:common/span_cov.glsl@

@INCLUDE:common/real_pow.glsl@

// Unbounded domain boundaries (injected from sympy one-sided limits): slowly diverging
// boundaries such as log(x) as x→0+. A fixed-width sampling window cannot sample deep
// enough, so the curve would vanish entirely in a deep viewport; here the polyline is
// extended along the boundary as a vertical ray to ±1e12, which reaches any depth and
// lies exactly on the boundary x.
float _edge_hit(float xa, float xb) {
    float best = 1e30;
@EDGEHIT@
    return best;
}
vec2 _edge_du(float ex) {
@EDGEDU@
    return vec2(0.0, 0.0);
}

// Distance (logical px) from a pixel to the boundary ray: the polyline's infinite extension
// along a slowly diverging domain boundary (log(x) as x→0+), which a fixed-width sampling
// window can never reach. nl/nh are the column's extremes — its own valid samples, or the
// probe taken just inside the domain when the column has none (see main). The ray is drawn
// only when those samples have *not* already reached that viewport edge: if the polyline
// already leaves the viewport it covers the visible part on its own, and extending it would
// be a connector. Two separate edges: `nl` is the samples' deepest point — it decides
// *whether* to draw — and `rtop` is where the down-ray starts — the descent carries every
// value below it to the boundary. (Conflating them draws a second wall next to the visible
// curve: measured as a fork at log(1+x)'s descent, x = −1 next to x = −0.996.)
float _ray_dist(vec2 p, float ex, vec2 du, float nl, float nh, float rtop,
                float y_top, float y_bot, float sx, float sy) {
    float d = 1e30;
    if (ex >= 1e29 || nl >= 1e29) {
        return d;
    }
    if (du.x != 0.0 && nl > y_bot) {
        d = min(d, _seg_dist(p, vec2(ex * sx, rtop * sy), vec2(ex * sx, -1e12)));
    }
    if (du.y != 0.0 && nh > -1e29 && nh < y_top) {
        d = min(d, _seg_dist(p, vec2(ex * sx, nh * sy), vec2(ex * sx, 1e12)));
    }
    return d;
}

#define F(u) (@FUNC@)
#define DF @DFUNC@      // f'(x): the march's slope bootstrap (NaN/garbage => probe instead)
// Domain predicate (pixels outside the domain are not drawn). DOMS is the per-sample
// variant: for a purely pole-shaped domain (tan, 1/x, sec, …) it is `true`, because
// there the NaN/inf test on the value already rejects out-of-domain samples.
@DOM@
@DOMS@
// The march's knobs: steps per direction (the budget — sustained structure exhausts
// it) and the join invariant's radius (a segment only ever joins samples this close).
#define MARCH 12
#define JOIN 2.5

void main() {
    // Camera -> world: the centre lands in the middle of the item, one logical pixel is
    // (camera.z, camera.w) world units, and vUV.y is top-down (screen y grows downwards).
    float x = camera.x + (vUV.x - 0.5) * size.x * camera.z;
    float y = camera.y + (0.5 - vUV.y) * size.y * camera.w;

    float R = lineWidth * 0.5 + 0.5;   // stroke radius + AA ramp (logical px)
    float BW = max(R - 0.75, 0.5);      // band feather: a filled edge, not a stroked one
    float spany = size.y * camera.w;   // visible y span (world units)
    float dx = camera.z;               // world x per pixel
    float sx = 1.0 / camera.z;         // pixels per world x
    float sy = 1.0 / camera.w;         // pixels per world y
    float y_top = camera.y + 0.5 * spany;
    float y_bot = camera.y - 0.5 * spany;

    // Legacy path (unsupported operations): the sampled envelope band plus the boundary ray.
    // Pixels outside the domain are still inside the curve's stroke band, so the ray/chords
    // below paint the half that the domain test would otherwise discard.
    // The boundary query is the sampled interval **widened by the stroke radius**
    // (lineWidth/2 + 0.5): the coverage ramp reaches that far, so a narrower window clips the
    // ray's own stroke (measured: the ray was evaluated only by the ±1-column band of pixels
    // and the descent's neighbours got distance to an endpoint — 1.00 device px instead of
    // 3.00). The constants are the sample offsets of the loop below (-1 + 0.25/4, -1 + 7.25/4).
    float edge_r = (lineWidth * 0.5 + 0.5) * dx;   // stroke radius, world units
    float ex_ = _edge_hit(x - 0.9375 * dx - edge_r, x + 0.8125 * dx + edge_r);
    vec2 du_ = (ex_ < 1e29) ? _edge_du(ex_) : vec2(0.0, 0.0);
    // A column with no valid sample (a pixel just outside the domain) still lies inside the
    // stroke's band around the boundary, so the band must be evaluated there too — but its
    // extent has to come from the function itself, probed just inside the domain, since every
    // sample of such a column is out of domain. Judging it by the sentinel alone drew a
    // full-height line at `log(x-1)`'s boundary in a shallow view (where the visible curve is
    // nowhere near the boundary), while ignoring those pixels altogether cost the descent its
    // whole outer half (1.00-1.67 of the nominal 3.00 device px) in a deep one.
    float edge_nl = 1e30;
    float edge_nh = -1e30;
    float edge_top = 1e30;   // the down-ray's top edge (the gate is edge_nl, not this)
    if (ex_ < 1e29) {
        // Probe a short ladder just inside the boundary on whichever side is in the
        // domain: the down-ray starts at the HIGHEST probe (the topmost value the
        // descent carries to the boundary), not the deepest — a single close probe
        // starts the ray too low and leaves a hole between it and the polyline's own
        // reach (measured: a 6-row gap at the foot of log(1+x)'s wall). The
        // shallow-boundary regression the probe exists for is preserved: with every
        // probe below the viewport bottom the ray still declines to draw (log(x-1) in
        // a shallow view stays clean), and a boundary with no in-domain probe at all
        // gets neither ray (the sentinels fail both `nl > y_bot` and `nh < y_top`).
        float best_down = -1e30;      // the down-ray's top edge
        float best_up = 1e30;         // the deepest value (the gate), and the up-ray's
        for (int k = 0; k < 4; k++) {  // bottom edge (the up-ray starts at its shallowest)
            for (int side = 0; side < 2; side++) {
                float sgn = side == 0 ? 1.0 : -1.0;
                float xt = ex_ + sgn * (float(k) + 0.25) * dx;
                float s = 1e30;
                if (DOMS(xt)) {
                    s = F(xt);
                }
                if (s == s && abs(s) < 1e30) {
                    best_down = max(best_down, s);
                    best_up = min(best_up, s);
                }
            }
        }
        edge_nl = best_up;
        edge_nh = best_down;
        edge_top = best_down;
    }

    if (!DOM(x)) {
        // A pixel just outside the domain is still inside the curve's stroke band:
        // near a boundary that is not (yet) vertical, the curve passes within a
        // stroke width at this pixel's height — one or two pixels inside. The ray
        // alone only covers the deep, genuinely vertical part (it starts at the
        // boundary probe), which left a hole between the two (measured: log(1+x)'s
        // descent wall lost its whole middle column, rows spanning the shallow
        // part of the view, until the just-inside probe chords were added here).
        float rcov = clamp(lineWidth * 0.5 + 0.5
                          - _ray_dist(vec2(x * sx, y * sy), ex_, du_, edge_nl, edge_nh,
                                      edge_top, y_top, y_bot, sx, sy),
                          0.0, 1.0);
        if (ex_ < 1e29) {
            // Just inside the boundary: four chord samples and their segments, the
            // same consistent-pair idea as the column probe, with the midpoint test
            // replaced by "the pair brackets this pixel's height" (a monotone
            // near-boundary stretch is exactly the case that qualifies).
            float pv[4];
            bool okv[4];
            for (int i = 0; i < 4; i++) {
                float xt = ex_ + (float(i) + 0.5) * dx;
                pv[i] = 1e30;
                if (DOMS(xt)) {
                    pv[i] = F(xt);
                }
                okv[i] = (pv[i] == pv[i]) && (abs(pv[i]) < 1e30);
            }
            for (int i = 0; i < 3; i++) {
                if (okv[i] && okv[i + 1]
                        && y >= min(pv[i], pv[i + 1]) && y <= max(pv[i], pv[i + 1])) {
                    float d = _seg_dist(vec2(x * sx, y * sy),
                                        vec2((ex_ + (float(i) + 0.5) * dx) * sx, pv[i] * sy),
                                        vec2((ex_ + (float(i + 1) + 0.5) * dx) * sx, pv[i + 1] * sy));
                    rcov = max(rcov, clamp(lineWidth * 0.5 + 0.5 - d, 0.0, 1.0));
                }
            }
        }
        fragColor = vec4(color.rgb * rcov * color.a * qt_Opacity, rcov * color.a * qt_Opacity);
        return;
    }

    // The march and the legacy band: only the unsupported-expression path still needs them.
    // The interval path is the fixed-x polyline above plus the exact column band, so it must
    // not pay for a single march evaluation.
    float f0 = F(x);
    float d1 = DF;                       // f'(x) at the pixel's own column
    bool have_m0 = (d1 == d1) && (abs(d1) < 1e30);
    float m0 = have_m0 ? d1 * camera.z / camera.w : 0.0;   // screen slope at the seed

    bool marched = false;

    // ---- the march: arc-length sampling along the curve, both directions ----
    // Seed: the curve at this pixel's own column. A pole or domain-edge column probes
    // just off itself and the nearest valid neighbour carries the march: a singularity
    // column such as sin(1/x)'s still needs its envelope band, while a plain pole
    // column of 1/x marches up its own branches and draws nothing at the pole.
    float xs = x;
    float ys = f0;
    bool have_seed = DOMS(x) && (ys == ys) && (abs(ys) < 1e30);
    if (!have_seed) {
        for (int k = 0; k < 4 && !have_seed; k++) {
            float mag = 0.25 * dx * float(k / 2 + 1);
            xs = x + ((k - (k / 2) * 2 == 0) ? mag : -mag);
            ys = 1e30;
            if (DOMS(xs)) {
                ys = F(xs);
            }
            have_seed = (ys == ys) && (abs(ys) < 1e30);
        }
    }

    vec2 pp = vec2(x * sx, y * sy);    // this pixel, screen space

    // Tangent-foot seed: on a steep curve the column's own curve point can be a
    // whole viewport away (in arc) from this pixel's foot, and no budget can walk
    // that far along the curve (measured: dashed strokes of one budget's length on
    // y = 10x). Project the pixel onto the tangent at the column seed — one dot
    // product, then one evaluation to land the seed on the curve there. This is the
    // coverage the old fixed-x renderer got from long sample-chords, which the join
    // invariant refuses by design. No cap on the projection: far along a near-vertical
    // tangent is exactly where a deep-zoomed pole's foot is (the two branches merge
    // into a wall), and a seed that lands nowhere near this pixel is filtered by the
    // hoplessness gate below, which is what the gate is for.
    if (have_seed && have_m0) {
        vec2 u = vec2(1.0, m0) / sqrt(1.0 + m0 * m0);     // unit tangent (screen px)
        float t = dot(pp - vec2(xs * sx, ys * sy), u);
        float xn2 = xs + t * u.x * dx;
        if (DOMS(xn2)) {
            float yn2 = F(xn2);
            if (yn2 == yn2 && abs(yn2) < 1e30) {
                xs = xn2;
                ys = yn2;
            }
        }
    }
    float dmin = 1e30;                 // stroke: min distance to the joined polyline
    float nl = 1e30;                   // the column's y extent (the boundary ray's start)
    float nh = -1e30;
    float turns = 0.0;                 // direction reversals along the march
    float unstable = 0.0;              // steps that missed their own tangent prediction
    bool unresolved = false;           // the budget ran out: sustained structure

    if (have_seed && distance(pp, vec2(xs * sx, ys * sy)) < R + 2.0 + float(MARCH)) {
        // The march is worth running only when the seeded curve point is within
        // walking distance of this pixel (the ball plus the budget): a seed further
        // away than that stands in for a pixel no curve point can serve, and marching
        // anyway burns the budget and fires a spurious band probe on background.
        nl = min(nl, ys);
        nh = max(nh, ys);
        for (int dir = -1; dir <= 1; dir += 2) {
            float lx = xs;             // the seed carries both directions
            float ly = ys;
            float m = m0;              // screen slope estimate (f' bootstraps it)
            bool run = have_m0;        // the slope is usable for a 1-px-arc stride
            float dprev = 1e30;        // previous step's distance from this pixel
            float pending = run ? 1.0 / sqrt(1.0 + m * m) : 0.25;   // next x stride (px)
            float pd = 0.0;            // previous joined Δy (screen) — turn detection
            float esc = 0.0;           // consecutive steps outside the pixel's R+1 ball
            int step = 0;
            for (; step < MARCH; step++) {
                float xn = lx + float(dir) * pending * dx;
                float yn = 1e30;      // out-of-domain steps stay unevaluated (the
                if (DOMS(xn)) {       // wide window's note on log(negative))
                    yn = F(xn);
                }
                if (!((yn == yn) && (abs(yn) < 1e30))) {
                    // A pole landing (measure-zero) or a domain edge. One wider retry
                    // steps over the former; a second failure is a domain edge, and
                    // this direction ends here (the boundary ray owns the far side).
                    xn = lx + float(dir) * (2.0 * pending + 0.25) * dx;
                    yn = 1e30;
                    if (DOMS(xn)) {
                        yn = F(xn);
                    }
                    if (!((yn == yn) && (abs(yn) < 1e30))) {
                        break;
                    }
                    run = false;
                    pending = 0.25;
                }
                nl = min(nl, yn);
                nh = max(nh, yn);
                vec2 pt = vec2(xn * sx, yn * sy);      // screen space
                vec2 last = vec2(lx * sx, ly * sy);
                // The join invariant: a segment only ever joins two samples within
                // JOIN px of each other. Chords of 1-px-arc steps are ~1.2 px on any
                // continuous curve; a jump across a pole — or across a plain
                // discontinuity the static analysis never saw — is hundreds of px.
                // The connector is not detected and cut, it cannot be formed.
                if (distance(pt, last) <= JOIN) {
                    dmin = min(dmin, _seg_dist(pp, last, pt));
                    float dy = pt.y - last.y;
                    if (run) {
                        // Tangent prediction: a smooth curve misses its own 1-px
                        // extrapolation by curvature only; aliasing misses by ~a px.
                        vec2 pred = last + float(dir) * vec2(1.0, m) / sqrt(1.0 + m * m);
                        if (distance(pt, pred) > 0.75) {
                            unstable += 1.0;
                        }
                        // A reversal may well come back: it resets the escape count.
                        // The noise floor matters: on a nearly-flat stretch the dy of a
                        // 1-px step is float noise (x/sqrt(x*x) moves ~1e-12 per step
                        // along a constant branch), whose sign flips read as turns and
                        // band the whole column (measured: a solid ±1 column at the
                        // function's jump).
                        if (pd * dy < 0.0 && abs(pd) > 1e-4 && abs(dy) > 1e-4) {
                            turns += 1.0;
                            esc = 0.0;
                        }
                    }
                    float xd = pt.x - last.x;
                    m = dy / (abs(xd) > 1e-12 ? xd : float(dir) * 1e-12);
                    pd = dy;
                    run = true;
                    pending = 1.0 / sqrt(1.0 + m * m);
                    lx = xn;
                    ly = yn;
                } else if (!run && pending > 0.01) {
                    // A *probe* step (establishing the slope) that cannot join: the
                    // curve is steeper than the probe assumed — halve until joinable
                    // (four halvings at most; f' bootstraps steeper than that).
                    pending *= 0.5;
                    continue;
                } else {
                    // No continuous path at an honest 1-px-arc stride (or a probe
                    // out of halvings): a discontinuity. Resume on the far branch —
                    // the join invariant has refused the connector.
                    run = false;
                    pd = 0.0;
                    pending = 0.25;
                    lx = xn;
                    ly = yn;
                }
                float dpt = distance(pt, pp);
                if (dpt > R + 1.0) {
                    // Outside the ball, but only *receding* steps count: a march
                    // that is still closing in on this pixel (a steep curve's foot
                    // is a long way down the curve from the column seed) must not
                    // be stopped on its way in.
                    esc = (dpt > dprev) ? esc + 1.0 : 0.0;
                    if (esc >= 3.0) {
                        break;         // receding for 3 steps, not coming back
                    }
                } else {
                    esc = 0.0;         // re-entered the pixel's ball (a sweep passes)
                }
                dprev = dpt;
                if (yn > y_top + (R + 1.0) * camera.w || yn < y_bot - (R + 1.0) * camera.w) {
                    break;             // past the viewport edge by the stroke radius
                }
            }
            if (step >= MARCH) {
                unresolved = true;    // the budget ran out: the march cannot get
                                     // through this in 1-px steps (sin(1/x)'s
                                     // singularity) — the envelope band's case
            }
        }
    }

    // ---- stroke ----
    // Legacy: distance to the joined polyline, the boundary ray included. A slowly diverging
    // boundary such as log(x)'s is an honest vertical asymptote, and a column with no
    // valid sample of its own borrows the probe from the ray setup above.
    float cov = clamp(R - min(dmin, _ray_dist(pp, ex_, du_, nl < 1e29 ? nl : edge_nl,
                                              nh > -1e29 ? nh : edge_nh,
                                              nh > -1e29 ? nh : edge_top,
                                              y_top, y_bot, sx, sy)), 0.0, 1.0);

    // ---- column probe: the structure a local march cannot see ----
    // A sub-column oscillation (sin(1/x) near its singularity) is *resolved* along
    // the arc — the march follows it faithfully for millions of pixels without ever
    // turning — while the column still packs whole periods between neighbouring
    // pixels. Only a fixed-x probe across the column window sees it: grid values at
    // 1-px spacing with the midpoints between them — a midpoint that falls outside
    // its pair's [min,max] is structure below the pair spacing (for independent
    // samples it lands outside 2 times out of 3). A pole landing corrupts exactly one
    // pair, which is why the trigger needs two violations; an oscillation corrupts
    // them all. The slope gate (|f'| > 1 screen unit) keeps the probe off flat
    // background — the same gate the old fixed-grid renderer used.
    float violations = 0.0;    // pairs with structure below the pair spacing
    float bracket = 0.0;       // soft window: a consistent pair that spans this pixel's y
    if (have_m0 && (abs(m0) > 1.0 || unresolved || turns >= 2.0 || unstable >= 4.0)) {
        // 1-px spacing is deliberate: the chord-stroke below needs pairs that SPAN
        // this pixel's y (a steep wall's column value sits a whole view away from
        // the pixel at hand), which only a coarse grid provides. A finer grid hugs
        // the column's own value and brackets nothing. (The very deep merged wall —
        // two branches within a stroke of each other under an enormous y view — has
        // its visible arc between the column and the pole, narrower than this step;
        // covering it needs a bisection seed towards the pole, which is a separate
        // mechanism.)
        float grid[9];
        bool okg[9];
        for (int i = 0; i < 9; i++) {
            float off = float(i) - 4.0;                  // -4 .. +4 px, 1-px spacing
            float s = 1e30;      // leave out-of-domain samples unevaluated (see the
            if (i == 4) {       // wide window's note: log(negative) is undefined, and a
                s = f0;         // discarded evaluation still perturbs the codegen)
            } else if (DOMS(x + off * dx)) {
                s = F(x + off * dx);
            }
            grid[i] = s;
            okg[i] = (s == s) && (abs(s) < 1e30);
        }
        for (int i = 0; i < 8; i++) {
            if (okg[i] && okg[i + 1]) {
                float xm = x + (float(i) - 3.5) * dx;    // the pair's midpoint
                float fm = 1e30;
                bool okm = false;
                if (DOMS(xm)) {
                    fm = F(xm);
                    okm = (fm == fm) && (abs(fm) < 1e30);
                }
                if (okm && (fm < min(grid[i], grid[i + 1])
                            || fm > max(grid[i], grid[i + 1]))) {
                    violations += 1.0;    // structure below the pair spacing
                } else if (okm && fm > min(grid[i], grid[i + 1]) && fm < max(grid[i], grid[i + 1])) {
                    // A consistent pair — its midpoint strictly inside its span — is a
                    // trustworthy chord: the connected curve between the pair's ends
                    // really does pass through the values in between (the jump pair
                    // of a pole/jump column just failed the midpoint test above, and
                    // a flat or degenerate span brackets nothing). Stroke those
                    // chords: it is the geometry that covers a steep monotone wall
                    // (1/x, tan at large |y|), whose foot is a whole view up the arc
                    // where no march budget can walk. The chords stay one stride
                    // long, so this is the old fixed-x renderer's coverage without
                    // its connector: the straddling pair never qualifies.
                    float dchord = _seg_dist(pp,
                        vec2((x + (float(i) - 4.0) * dx) * sx, grid[i] * sy),
                        vec2((x + (float(i + 1) - 4.0) * dx) * sx, grid[i + 1] * sy));
                    bracket = max(bracket, clamp(R - dchord, 0.0, 1.0));
                }
            }
        }
    }

    // ---- envelope band: the columns the march could not resolve ----
    // The budget ran out (sustained structure), the path reversed on itself twice
    // within the window (a sub-stroke oscillation), it kept missing its own tangent
    // prediction (aliasing), or the column probe found structure below the pair
    // spacing (a sub-column oscillation): fill the measured envelope instead. The
    // edges come from a wider ±8-column, 16-point window — more samples are more
    // likely to hit the extrema, so the edges do not bite dark seams/steps (measured:
    // 2.4 px between neighbouring columns once this was added).
    // Every trigger's weight tapers with its count, so the region's edge is feathered
    // rather than a hard cut — and the deliberate 2-px overshoot of the fill's ramp
    // (the comb-smoothing trade-off) stays feathered there (measured: 45 solid pixels
    // beyond ±1 for the old renderer, of a 50 budget). The exhausted budget is a
    // binary, all-or-nothing signal: full weight.
    // Everything is hoisted out of the trigger's `if` on purpose: scoped variants let
    // the optimiser re-derive the coverage differently per source edit, and a shader
    // whose output changes under an unrelated edit is a shader no one can debug.
    float band_w = unresolved ? 1.0
        : max(max(bracket, max(smoothstep(2.0, 4.0, turns),
                               smoothstep(4.0, 6.0, unstable))),
              smoothstep(2.0, 4.0, violations));
    float wl = 1e30;
    float wh = -1e30;
    // Same construction as the column probe on purpose — same inline expression, same
    // inline guards: a `t`-indexed variant of this window computed out-of-envelope
    // samples under this driver's optimiser (measured: wh−wl came out 4+ world units
    // where the true spread was 3.2, and the band collapsed to zero).
    for (int i = 0; i < 17; i++) {
        float off = float(i) - 8.0;                     // -8 .. +8 px, 1-px spacing
        float s = 1e30;
        if (DOMS(x + off * dx)) {
            s = F(x + off * dx);
        }
        if (s == s && abs(s) < 1e30) {
            wl = min(wl, s);
            wh = max(wh, s);
        }
    }
    if (band_w > 0.0) {
        // The 1.5-px feather (down from 2 in the fixed-grid renderer, whose band
        // region was ~18 % narrower): enough to smooth the column-to-column edges,
        // and the overshoot beyond the envelope stays under the solid threshold.
        float band = clamp((wh - y) * sy + 1.5, 0.0, 1.0)
                   * clamp((y - wl) * sy + 1.5, 0.0, 1.0);
        // The dense-chaos gate: a pole column's or a jump column's envelope spans
        // the whole viewport without taking none of those values (the window's samples
        // form a monotone "V" through the jump — exactly one violating pair), while
        // a genuinely oversampled oscillation violates most of its pairs. The count
        // is the gate (the old renderer's 32×-viewport gap, without recognising the
        // pole — and unlike a world-unit gate it does not drift with zoom). The
        // smoothstep on the envelope span keeps a band narrower than the stroke
        // itself pointless: the polyline already covers it — a resolvable extremum
        // must stay a stroke, not become a block.
        cov = max(cov, band * band_w * smoothstep(1.5, 4.0, (wh - wl) * sy)
                            * max(smoothstep(3.0, 6.0, violations), bracket));
    }

    float a = cov * color.a * qt_Opacity;
    if (isnan(f0) || isinf(f0)) {
        a = 0.0;                       // discard points like sin(1/0), 1/0 outright
    }
    fragColor = vec4(color.rgb * a, a);   // premultiplied alpha
}
