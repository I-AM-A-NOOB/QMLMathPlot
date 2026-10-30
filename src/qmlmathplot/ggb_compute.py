"""GLSL generation for the sequential GPU port of GGB's interval path.

One compute invocation walks the screen columns in order (like ``IntervalPath``), evaluates
the interval of the function on each column and emits up to two quads into a storage buffer.
The geometry is drawn by a tiny vertex/fragment pair; no CPU sampling is involved.
"""

from __future__ import annotations

import sympy as sp

from .interval import INTERVAL_LIB, interval_glsl
from .model import _pole_glsl, domain_glsl, func_glsl, has_sign_pole

__all__ = ["PATH_VERTEX", "PATH_FRAGMENT", "path_compute_source", "path_subsamples"]

PATH_VERTEX = """#version 440
layout(location = 0) in vec2 pos;
layout(location = 1) in vec4 color;
layout(location = 0) out vec4 vColor;
void main() {
    gl_Position = vec4(pos, 0.0, 1.0);
    vColor = color;
}
"""

PATH_FRAGMENT = """#version 440
layout(location = 0) in vec4 vColor;
layout(location = 0) out vec4 fragColor;
void main() { fragColor = vColor; }
"""


def path_subsamples(expression: str) -> int:
    # log's near-vertical descent showed a two-level step at 4x; 8x halves it.
    return 8


def path_compute_source(expression: str) -> str:
    expr = sp.sympify(expression)
    subsamples = path_subsamples(expression)
    func = func_glsl(expr, "u", wrap=True)
    domain = domain_glsl(expr, "u") or "true"
    intervals = interval_glsl(expr)
    pole = _pole_glsl(expr, "u") if has_sign_pole(expr) else "1.0"
    has_pole = "1" if has_sign_pole(expr) else "0"
    return f"""#version 440
layout(local_size_x = 1) in;
layout(std140, binding = 0) uniform buf {{
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 camera;
    vec2 size;
    float lineWidth;
    vec4 color;
}};
layout(std430, binding = 1) buffer V {{
    float verts[];
}};
#define TAU 6.283185307179586
float _wrap(float t) {{ return t - TAU * floor(t * (1.0 / TAU)); }}
#define SIN(t) sin(_wrap(t))
#define COS(t) cos(_wrap(t))
#define F(u) ({func})
#define DOM(u) ({domain})
#define POLE(u) ({pole})
#define HAS_SIGN_POLE {has_pole}
{INTERVAL_LIB}
float _real_pow(float v, float p) {{
    return v < 0.0 ? -pow(-v, p) : pow(v, p);
}}
{intervals}

float sy(float y) {{ return (camera.y - y) / camera.w + 0.5 * size.y; }}
vec2 ndc(vec2 p) {{
    return vec2(p.x / size.x * 2.0 - 1.0, 1.0 - p.y / size.y * 2.0);
}}
void put(int col, int quad, int corner, vec2 p, vec4 c) {{
    int base = ((col * 2 + quad) * 6 + corner) * 6;
    vec2 n = ndc(p);
    verts[base + 0] = n.x;
    verts[base + 1] = n.y;
    verts[base + 2] = c.r * c.a;
    verts[base + 3] = c.g * c.a;
    verts[base + 4] = c.b * c.a;
    verts[base + 5] = c.a;
}}
void slot(int col, int quad, vec2 a, vec2 b) {{
    vec2 d = b - a;
    float len = length(d);
    vec2 nrm = len > 1e-6 ? vec2(-d.y, d.x) / len * lineWidth * 0.5 : vec2(0.0);
    vec4 c = color;
    put(col, quad, 0, a + nrm, c);
    put(col, quad, 1, a - nrm, c);
    put(col, quad, 2, b - nrm, c);
    put(col, quad, 3, a + nrm, c);
    put(col, quad, 4, b - nrm, c);
    put(col, quad, 5, b + nrm, c);
}}
void main() {{
    int cols = int(max(1.0, size.x) * float({subsamples}));
    float dx = camera.z / float({subsamples});
    float xmin = camera.x - 0.5 * size.x * camera.z;
    float y_top = camera.y + 0.5 * size.y * camera.w;
    float y_bot = camera.y - 0.5 * size.y * camera.w;
    for (int col = 0; col < cols; col++) {{
        // Start each column as two degenerate quads; the topology fills one or both.
        for (int q = 0; q < 2; q++) {{
            for (int k = 0; k < 6; k++) {{
                put(col, q, k, vec2(10.0), vec4(0.0));
            }}
        }}
        float x0 = xmin + float(col) * dx;
        float x1 = x0 + dx;
        float px0 = float(col) / float({subsamples});
        float px1 = float(col + 1) / float({subsamples});
        bool pole_col = HAS_SIGN_POLE != 0 && POLE(x0) * POLE(x1) <= 0.0;
        IVal v = FI(x0, x1);
        if (v.kind == IK_EMPTY) {{
            continue;
        }}
        // A pole wrapped by a monotone map (exp(tan)) produces a connected interval that
        // would bridge the two branches; only that topology is skipped. Inverted/whole
        // intervals still carry the one-sided rays.
        if (pole_col && v.kind == IK_CONNECTED) {{
            continue;
        }}
        if (abs(camera.z) > 1.0 && v.kind == IK_WHOLE) {{
            // Only a genuinely whole column is GGB's dense fill. Filling every
            // non-connected/inverted column made ordinary functions flood when zoomed out.
            slot(col, 0, vec2(px0, 0.0), vec2(px0, size.y));
            continue;
        }}
        if (v.kind == IK_WHOLE) {{
            slot(col, 0, vec2(px0, 0.0), vec2(px0, size.y));
        }} else if (v.kind == IK_INVERTED) {{
            if (v.hi < y_top) {{
                slot(col, 0, vec2(px1, 0.0), vec2(px1, sy(v.hi)));
            }}
            if (v.lo > y_bot) {{
                slot(col, 1, vec2(px0, size.y), vec2(px0, sy(v.lo)));
            }}
        }} else {{
            vec2 a;
            vec2 b;
            if (v.lo <= -IINF && v.hi >= IINF) {{
                slot(col, 0, vec2(px0, 0.0), vec2(px0, size.y));
                continue;
            }} else if (v.lo <= -IINF) {{
                a = vec2(px0, size.y);
                b = vec2(px1, sy(v.hi));
            }} else if (v.hi >= IINF) {{
                a = vec2(px0, sy(v.lo));
                b = vec2(px1, 0.0);
            }} else {{
                float y0 = v.lo;
                float y1 = v.hi;
                bool ft = false;
                float f0 = 0.0;
                float f1 = 0.0;
                if (DOM(x0) && DOM(x1)) {{
                    f0 = F(x0);
                    f1 = F(x1);
                    ft = f0 == f0 && f1 == f1 && abs(f0) < 1e30 && abs(f1) < 1e30;
                    if (ft) {{
                        y0 = f0;
                        y1 = f1;
                    }}
                }}
                // A column whose interval is much wider than its endpoint chord is
                // unresolved oscillation (sin(1/x) near zero): fill the attained interval
                // instead of drawing a comb of per-subcolumn chords. A steep monotone
                // segment has a large endpoint chord, so it stays a stroke.
                float span = (v.hi - v.lo) * (1.0 / camera.w);
                float edge = ft ? abs(f1 - f0) / camera.w : span;
                int turns = 0;
                if (span > 2.5 * lineWidth) {{
                    float prev = f0;
                    float prevd = 0.0;
                    for (int j = 1; j < 5; j++) {{
                        float xm = x0 + dx * float(j) * 0.25;
                        if (!DOM(xm)) {{
                            continue;
                        }}
                        float fm = F(xm);
                        if (!(fm == fm) || abs(fm) >= 1e30) {{
                            continue;
                        }}
                        float d = fm - prev;
                        if (abs(d) > 1e-4 && prevd * d < 0.0) {{
                            turns += 1;
                        }}
                        prevd = d;
                        prev = fm;
                    }}
                }}
                if (span > 2.5 * lineWidth && (edge < 0.5 * span || turns >= 2)) {{
                    slot(col, 0, vec2(px0, sy(v.hi)), vec2(px0, sy(v.lo)));
                    continue;
                }}
                a = vec2(px0, sy(y0));
                b = vec2(px1, sy(y1));
            }}
            slot(col, 0, a, b);
        }}
    }}
}}
"""
