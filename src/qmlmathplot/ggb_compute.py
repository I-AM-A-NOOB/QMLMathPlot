"""GLSL generation for the sequential GPU port of GGB's interval path.

One compute invocation walks the screen columns in order (like ``IntervalPath``), evaluates
the interval of the function on each column and emits up to two quads into a storage buffer.
The geometry is drawn by a tiny vertex/fragment pair; no CPU sampling is involved.
"""

from __future__ import annotations

import sympy as sp

from .interval import INTERVAL_LIB, interval_glsl
from .model import domain_glsl, func_glsl

__all__ = ["PATH_VERTEX", "PATH_FRAGMENT", "path_compute_source"]

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


def path_compute_source(expression: str) -> str:
    expr = sp.sympify(expression)
    func = func_glsl(expr, "u", wrap=True)
    domain = domain_glsl(expr, "u") or "true"
    intervals = interval_glsl(expr)
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
{INTERVAL_LIB}
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
    int cols = int(max(1.0, size.x));
    float dx = camera.z;
    float xmin = camera.x - 0.5 * size.x * dx;
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
        float px0 = float(col);
        float px1 = float(col + 1);
        IVal v = FI(x0, x1);
        if (v.kind == IK_EMPTY) {{
            continue;
        }}
        if (abs(camera.z) > 1.0) {{
            // Undersampled: several poles per column. Like GGB's dense interval branch,
            // fill the attained column instead of trying to keep the sparse branch edges.
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
                if (DOM(x0) && DOM(x1)) {{
                    float f0 = F(x0);
                    float f1 = F(x1);
                    if (f0 == f0 && f1 == f1 && abs(f0) < 1e30 && abs(f1) < 1e30) {{
                        y0 = f0;
                        y1 = f1;
                    }}
                }}
                a = vec2(px0, sy(y0));
                b = vec2(px1, sy(y1));
            }}
            slot(col, 0, a, b);
        }}
    }}
}}
"""
