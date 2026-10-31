#version 440
layout(location = 0) in vec2 vUV;
layout(location = 0) out vec4 fragColor;
@INCLUDE:common/uniforms.glsl@

@INCLUDE:common/arg_reduction.glsl@

@INCLUDE:common/real_pow.glsl@

@INCLUDE:common/seg_dist.glsl@

// F is a macro with two parameters (text substitution; the call always passes the plain pixel
// coordinates, so no extra parentheses are needed on the arguments — see the note in the 1-D
// template). FX/FY are function-like too, because the one-sided boundary extension evaluates the
// gradient at a nearby in-domain point. A partial that is not a single term, e.g.
// d/dx (x-1)^2 = 2*x - 2, is wrapped so it can stand inside a product.
#define F(x, y) (@FUNC@)
#define FX(x, y) (@DFUNCX@)
#define FY(x, y) (@DFUNCY@)
#define DOM(x, y) (@DOMXY@)
// Product of the expression's pole factors (cos(y) for tan(y), a denominator for 1/(...)).
// A probe that crosses a zero of this product crossed a singularity, not a continuous chord.
#define POLE(x, y) (@POLEXY@)

void main() {
    float x = camera.x + (vUV.x - 0.5) * size.x * camera.z;
    float y = camera.y + (0.5 - vUV.y) * size.y * camera.w;
    float R = lineWidth * 0.5 + 0.5;

    bool dom = DOM(x, y);
    float f = 0.0;
    float gx = 0.0;
    float gy = 0.0;
    if (dom) {
        f = F(x, y);
        // |grad F| in pixels: the partial per world unit times the world units per pixel
        gx = FX(x, y) * camera.z;
        gy = FY(x, y) * camera.w;
    }

    // Distance to the level set, in pixels. Outside the domain the level set is still a
    // geometric object: a stroke is a band around it, so the domain clips the *curve*, not the
    // band. Without this, x - log(y-1) loses its lower half as the curve approaches y = 1
    // (the visible curve sits within one stroke radius of the boundary), and y - log(x) does
    // the same as x -> 0+. Evaluate the first-order model at the nearest in-domain point and
    // extrapolate it to this pixel.
    float d = 1e30;
    if (dom) {
        float gnorm = max(sqrt(gx * gx + gy * gy), 1e-9);
        d = abs(f) / gnorm;
    } else {
        for (int i = 0; i < 8; i++) {
            float a = float(i) * 0.7853981634;
            float ux = cos(a);
            float uy = sin(a);
            for (int k = 1; k <= 3; k++) {
                float rr = R * 0.5 * float(k);
                float qx = x + ux * rr * camera.z;
                float qy = y + uy * rr * camera.w;
                if (DOM(qx, qy)) {
                    float qf = F(qx, qy);
                    float qfx = FX(qx, qy);
                    float qfy = FY(qx, qy);
                    bool okq = (qf == qf) && (abs(qf) < 1e30)
                            && (qfx == qfx) && (qfy == qfy);
                    if (okq && qfx * qfx + qfy * qfy > 1e-18) {
                        // Project q onto the level set and measure the distance from p to
                        // that point. Extrapolating q's linear model to p instead is wrong
                        // across a boundary: log's model is only local, and far out it folds
                        // back onto the visible branch as a fake line.
                        float sx = qx;
                        float sy = qy;
                        for (int it = 0; it < 2; it++) {
                            float ff = F(sx, sy);
                            float fx = FX(sx, sy);
                            float fy = FY(sx, sy);
                            float den = fx * fx + fy * fy;
                            if (den > 1e-18) {
                                float tx = sx - ff * fx / den;
                                float ty = sy - ff * fy / den;
                                // Damp a step that leaves the domain: near log's boundary
                                // the full Newton step overshoots into x < 0, although the
                                // root is a fraction of a pixel inside it.
                                for (int h = 0; h < 4 && !DOM(tx, ty); h++) {
                                    tx = 0.5 * (sx + tx);
                                    ty = 0.5 * (sy + ty);
                                }
                                sx = tx;
                                sy = ty;
                            }
                        }
                        float nx = (sx - qx) / camera.z;
                        float ny = (sy - qy) / camera.w;
                        if (nx * nx + ny * ny <= (R + rr + 1.0) * (R + rr + 1.0)
                                && DOM(sx, sy)) {
                            // A projection is only useful if it converged: the residual at
                            // the new point must be below a quarter stroke radius.
                            float rf = F(sx, sy);
                            float rgx = FX(sx, sy) * camera.z;
                            float rgy = FY(sx, sy) * camera.w;
                            float rg = max(sqrt(rgx * rgx + rgy * rgy), 1e-9);
                            bool okr = (rf == rf) && (abs(rf) < 1e30)
                                    && (abs(rf) <= 0.25 * R * rg);
                            if (okr) {
                                float dxp = (x - sx) / camera.z;
                                float dyp = (y - sy) / camera.w;
                                d = min(d, sqrt(dxp * dxp + dyp * dyp));
                            }
                        }
                    }
                }
            }
        }
    }

    // ---- GeoGebra-style marching-square cell (GPU) ----
    // A pixel is one marching cell. GGB's AdaptiveQuadTree / Bernstein plotters never trust a
    // single point sample: they use the four corner signs and interpolate the crossings. The
    // first-order distance above is still the sub-pixel stroke; this cell test adds the
    // coverage a cell whose centre projection fails (a thin or tangent branch) would lose.
    // It is evaluated only when all four corners are in the domain and no pole factor changes
    // sign between them — a pole crossing a cell is a jump, not a contour segment.
    float march_cov = 0.0;
    if (dom) {
        float x0 = x - 0.5 * camera.z;
        float x1 = x + 0.5 * camera.z;
        float y0 = y - 0.5 * camera.w;
        float y1 = y + 0.5 * camera.w;
        bool okc = DOM(x0, y0) && DOM(x1, y0) && DOM(x0, y1) && DOM(x1, y1);
        float f00 = 0.0;
        float f10 = 0.0;
        float f11 = 0.0;
        float f01 = 0.0;
        if (okc) {
            f00 = F(x0, y0);
            f10 = F(x1, y0);
            f11 = F(x1, y1);
            f01 = F(x0, y1);
            okc = (f00 == f00) && (abs(f00) < 1e30)
               && (f10 == f10) && (abs(f10) < 1e30)
               && (f11 == f11) && (abs(f11) < 1e30)
               && (f01 == f01) && (abs(f01) < 1e30);
        }
        if (okc) {
            float p00 = POLE(x0, y0);
            float p10 = POLE(x1, y0);
            float p11 = POLE(x1, y1);
            float p01 = POLE(x0, y1);
            okc = p00 * p10 > 0.0 && p10 * p11 > 0.0
               && p11 * p01 > 0.0 && p01 * p00 > 0.0;
        }
        if (okc) {
            // corner order: bottom-left, bottom-right, top-right, top-left. Screen positions
            // use the same order (screen y grows down, so the bottom corners are +0.5).
            float cf[4] = float[4](f00, f10, f11, f01);
            vec2 cp[4] = vec2[4](vec2(-0.5, 0.5), vec2(0.5, 0.5),
                                 vec2(0.5, -0.5), vec2(-0.5, -0.5));
            vec2 pts[4];
            int pedge[4];
            int npts = 0;
            for (int e = 0; e < 4; e++) {
                int b = (e + 1) & 3;
                if ((cf[e] >= 0.0) != (cf[b] >= 0.0)) {
                    float tt = cf[e] / (cf[e] - cf[b]);
                    pts[npts] = mix(cp[e], cp[b], clamp(tt, 0.0, 1.0));
                    pedge[npts] = e;
                    npts++;
                }
            }
            if (npts == 2) {
                march_cov = clamp(R - _seg_dist(vec2(0.0), pts[0], pts[1]), 0.0, 1.0);
            } else if (npts == 4) {
                // The saddle case: use the cell centre to choose the asymptotic pairing.
                // If corner 0 has the centre's sign, the isolated corners are 1 and 3
                // (pair 0-1 and 2-3); otherwise they are 0 and 2 (pair 3-0 and 1-2).
                bool same = (cf[0] >= 0.0) == (f >= 0.0);
                int pa = same ? 0 : 3;
                int pb = same ? 1 : 0;
                int pc = same ? 2 : 1;
                int pd = same ? 3 : 2;
                vec2 a0 = vec2(0.0);
                vec2 a1 = vec2(0.0);
                vec2 b0 = vec2(0.0);
                vec2 b1 = vec2(0.0);
                for (int i = 0; i < 4; i++) {
                    if (pedge[i] == pa) a0 = pts[i];
                    if (pedge[i] == pb) a1 = pts[i];
                    if (pedge[i] == pc) b0 = pts[i];
                    if (pedge[i] == pd) b1 = pts[i];
                }
                march_cov = max(clamp(R - _seg_dist(vec2(0.0), a0, a1), 0.0, 1.0),
                                clamp(R - _seg_dist(vec2(0.0), b0, b1), 0.0, 1.0));
            }
        }
    }

    // A singular cell (|grad F| -> 0, GGB's ImplicitCurveMarchingRect.singular) has no
    // sign change and no useful Newton direction. Draw its tiny disc so an isolated point
    // such as x² + y² = 0 is not left to the projection alone.
    // GGB measures the singular cell in *world* derivative units (|Fx| + |Fy| < 0.001);
    // the per-pixel gradient of an ordinary circle is ~0.01 and must not qualify.
    // The distance gate is essential for a scaled expression: (x+y)/(x*y) has |F| < 0.05 and
    // |Fx|+|Fy| < 0.001 over huge regions around ±50, although the level set is far away. A
    // singular point must also be *on* the set to the first order: |F| ~ R*|grad F|. The
    // ratio is scale-invariant, unlike the two absolute thresholds above.
    float gw_s = abs(FX(x, y)) + abs(FY(x, y));
    float gnorm_s = max(sqrt(gx * gx + gy * gy), 1e-9);
    if (dom && (f == f) && (abs(f) < 1e30) && gw_s < 1e-3 && abs(f) < 0.05
            && abs(f) < R * gnorm_s) {
        march_cov = 1.0;
    }

    float cov = 0.0;
    if (dom) {
        float gnorm = max(sqrt(gx * gx + gy * gy), 1e-9);
        float nx = gx / gnorm;
        float ny = gy / gnorm;
        float stepx = nx * R * camera.z;
        float stepy = ny * R * camera.w;
        // Probe coordinates are hoisted into locals first: F's macro body is printed bare
        // (`x*x`, not `(u)*(u)`), so a shifted argument would expand textually as
        // `x + stepx*x + stepx` — AGENTS item 1, re-armed.
        float pxp = x + stepx;
        float pyp = y + stepy;
        float pxm = x - stepx;
        float pym = y - stepy;

        // Pole guard: a simple pole (tan) is monotone across the jump, so the first-order
        // distance can be a pseudo-zero and paint the asymptote (measured: a horizontal
        // connector at y = pi/2 + k*pi when the y view is wide). A probe that crosses a
        // zero of the pole product is not a continuous chord.
        float pole = POLE(x, y);
        bool crosses = pole * POLE(pxp, pyp) <= 0.0
                    || pole * POLE(pxm, pym) <= 0.0;

        // Certify the first-order distance by *projecting onto the level set* (Newton):
        // |F|/|grad F| is a pseudo-zero at a pole and at a diverging domain boundary (log),
        // where the value is large but the gradient is far larger. A converged projection
        // leaves a residual below a quarter stroke radius, and its distance to the pixel is
        // the actual distance. This is the 1-D march's tangent-foot seed, one dimension up.
        bool ok = false;
        float dproj = 1e30;
        bool finite_here = (f == f) && (abs(f) < 1e30);
        if (finite_here && !crosses && abs(f) / gnorm < R + 1.0) {
            float sx = x;
            float sy = y;
            for (int it = 0; it < 2; it++) {
                float ff = F(sx, sy);
                float fx = FX(sx, sy);
                float fy = FY(sx, sy);
                float den = fx * fx + fy * fy;
                if (den > 1e-18) {
                    float tx = sx - ff * fx / den;
                    float ty = sy - ff * fy / den;
                    for (int h = 0; h < 4 && !DOM(tx, ty); h++) {
                        tx = 0.5 * (sx + tx);
                        ty = 0.5 * (sy + ty);
                    }
                    sx = tx;
                    sy = ty;
                }
            }
            float rf = F(sx, sy);
            float rfx = FX(sx, sy) * camera.z;
            float rfy = FY(sx, sy) * camera.w;
            float rg = max(sqrt(rfx * rfx + rfy * rfy), 1e-9);
            bool conv = (rf == rf) && (abs(rf) < 1e30) && DOM(sx, sy)
                    && abs(rf) <= 0.25 * R * rg;
            if (conv) {
                float dxp = (x - sx) / camera.z;
                float dyp = (y - sy) / camera.w;
                dproj = sqrt(dxp * dxp + dyp * dyp);
                ok = dproj <= R + 0.5;
            }
        }
        cov = max(ok ? clamp(R - dproj, 0.0, 1.0) : 0.0, march_cov);
    } else {
        cov = max(clamp(R - d, 0.0, 1.0), march_cov);
    }

    float a = cov * color.a * qt_Opacity;
    fragColor = vec4(color.rgb * a, a);   // premultiplied alpha
}
