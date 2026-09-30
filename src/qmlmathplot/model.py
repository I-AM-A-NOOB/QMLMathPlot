"""Model 层：表达式 -> GLSL、着色器源码模板、视图矩形（平移/缩放）数学。

这一层不 import 任何 Qt，可以脱离 GUI 单独测试；ViewModel（PlotController）
和 View（QML 的 ShaderEffect）都建立在它上面。

绘图模型是**隐式（signed-distance）**：片元着色器逐像素算"到曲线的一阶屏幕
空间距离"，每帧代价 ∝ 像素数、与函数频率无关（振荡函数不会因为混叠把帧率拖
垮）；一个像素里塞进多个振荡的列改填 [min,max] 包络带 —— 见 FRAGMENT_TEMPLATE
上面的注释。
"""

from __future__ import annotations

import sympy as sp
from sympy.printing.glsl import GLSLPrinter
from sympy.printing.precedence import PRECEDENCE

__all__ = [
    "DEFAULT_VIEW",
    "FRAGMENT_TEMPLATE",
    "VERTEX_SHADER",
    "ViewRect",
    "dfunc_glsl",
    "func_glsl",
    "shader_sources",
]

# 默认视图（世界坐标）
DEFAULT_VIEW = (-6.0, 6.0, -2.0, 2.0)


class _PlotGLSLPrinter(GLSLPrinter):
    """GLSL printer 的补丁：小整数次幂不生成 pow()。

    GLSL 的 pow(x, y) 在 x < 0 时未定义（不少驱动直接给 NaN），而 sympy 会把
    x**2 印成 pow(x, 2.0)。这里改成连乘，顺带更快。
    """

    def _print_Pow(self, expr: sp.Expr) -> str:
        exp = expr.exp
        if exp.is_Integer and 2 <= abs(int(exp)) <= 8:
            n = int(exp)
            base = self.parenthesize(expr.base, PRECEDENCE["Mul"])
            product = "*".join([base] * abs(n))
            # 一律加括号：产物可能落在分母里（sympy 会把 x**-2 拆成 1/x**2），
            # 不加括号会印出 "a/x*x" 这种被解析成 (a/x)*x 的错式子
            return f"(1.0/({product}))" if n < 0 else f"({product})"
        return super()._print_Pow(expr)


def func_glsl(expr: sp.Expr, name: str = "x") -> str:
    """把 sympy 表达式印成 GLSL 表达式（返回 f(x) 的右值文本）。

    name 用于改名：着色器里 ``#define F(u) (...)`` 要把变量印成 u。
    """
    if name != "x":
        expr = expr.subs(sp.Symbol("x"), sp.Symbol(name))
    return _PlotGLSLPrinter().doprint(expr)


def dfunc_glsl(expr: sp.Expr, name: str = "x") -> str:
    """f'(x) 的 GLSL 文本（隐式绘图算屏幕空间距离要用）。"""
    return func_glsl(sp.diff(expr, sp.Symbol("x")), name)


class ViewRect:
    """视图矩形（世界坐标）+ 平移/缩放运算，由 ViewModel 持有。"""

    ZOOM_PER_STEP = 0.9  # 上滚一档：跨度 ×0.9（放大 10%）
    MIN_SPAN = 1e-9  # 跨度上下限，避免缩到 0（再也滚不回来）或 inf
    MAX_SPAN = 1e9

    __slots__ = ("xmin", "xmax", "ymin", "ymax")

    def __init__(self, xmin: float = -6.0, xmax: float = 6.0,
                 ymin: float = -2.0, ymax: float = 2.0) -> None:
        self.xmin, self.xmax, self.ymin, self.ymax = xmin, xmax, ymin, ymax

    # ---- 只读视图 ----
    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.xmin, self.xmax, self.ymin, self.ymax)

    def reset(self) -> None:
        self.xmin, self.xmax, self.ymin, self.ymax = DEFAULT_VIEW

    def world_at(self, u: float, v: float) -> tuple[float, float]:
        """归一化屏幕位置 (0~1, 左上原点) 对应的世界坐标。"""
        return (self.xmin + (self.xmax - self.xmin) * u,
                self.ymax - (self.ymax - self.ymin) * v)

    # ---- 平移 ----
    def pan_pixels(self, dx: float, dy: float, width: float, height: float) -> None:
        """按像素位移平移（屏幕 y 向下，世界 y 向上）。"""
        if width <= 0 or height <= 0:
            return
        world_dx = (self.xmax - self.xmin) * dx / width
        world_dy = (self.ymax - self.ymin) * dy / height
        self.xmin -= world_dx
        self.xmax -= world_dx
        self.ymin += world_dy
        self.ymax += world_dy

    # ---- 缩放 ----
    def zoom(self, delta: float, u: float, v: float) -> None:
        """以归一化位置 (u, v) 为锚点缩放；delta 为滚轮增量（120 = 一档）。

        缩放比例只由 delta 决定，因此同一位置的上下滚严格互逆；锚点按"到锚点
        的距离"缩放，所以光标下的世界点不会被平移走。
        """
        if delta == 0:
            return
        scale = self.ZOOM_PER_STEP ** (delta / 120.0)

        span_x = self.xmax - self.xmin
        span_y = self.ymax - self.ymin
        anchor_x, anchor_y = self.world_at(u, v)

        span_x = min(max(span_x * scale, self.MIN_SPAN), self.MAX_SPAN)
        span_y = min(max(span_y * scale, self.MIN_SPAN), self.MAX_SPAN)

        self.xmin = anchor_x - span_x * u
        self.xmax = anchor_x + span_x * (1.0 - u)
        self.ymin = anchor_y - span_y * (1.0 - v)
        self.ymax = anchor_y + span_y * v


# --------------------------------------------------------------------------
# 着色器源码（片元着色器 + 逐像素隐式绘图）
# --------------------------------------------------------------------------

VERTEX_SHADER = """#version 440
layout(location = 0) in vec4 qt_Vertex;
layout(location = 1) in vec2 qt_MultiTexCoord0;
layout(location = 0) out vec2 vUV;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
};
void main() {
    vUV = qt_MultiTexCoord0;
    gl_Position = qt_Matrix * qt_Vertex;
}
"""

# 隐式（signed-distance）绘图 + 欠采样包络带 —— 视觉上"完美"的两块拼图：
#
#   1) 描边：每像素算它到曲线的一阶屏幕空间距离（用 f 和 f'），
#      得到等宽、抗锯齿的线；陡峭段也不会变粗或断裂。
#   2) 包络带：当一个像素的 x 区间里塞进了多个振荡（局部周期 < 1 像素）时，
#      逐像素距离已经没有意义（画出来是摩尔纹/随机锯齿）。此时改为把
#      [min,max] 区间填成实心带 —— 对 sin(1/x) 就等价于填它的真实 ±1 包络。
#      判据是"列内全变差 tv >> 包络跨度 spread"（曲线在列内折返了）。
#
# 表达式用宏而不是用户函数：一个像素里要对同一个 x 求值 9 次（描边 1 次 + 包络
# 判据 8 次），宏是预处理展开、没有函数调用语义，正好合适。
# （曾经把"带用户函数的版本在 Intel D3D11 上挂死"记在这里——那是误诊：真因是
#  qsb 把片元源码按 .glsl 后缀烘成了顶点着色器，见 qsb.bake 的注释。）
FRAGMENT_TEMPLATE = """#version 440
layout(location = 0) in vec2 vUV;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 view;        // xmin, xmax, ymin, ymax
    vec2 size;        // 视口尺寸（逻辑像素）
    float lineWidth;  // 线宽（逻辑像素）
    vec4 color;       // 非预乘 rgba
};

#define F(u) (@FUNC@)
#define DF @DFUNC@

void main() {
    float x = mix(view.x, view.y, vUV.x);
    float y = mix(view.w, view.z, vUV.y);

    float spanx = view.y - view.x;
    float spany = view.w - view.z;
    float dx = spanx / size.x;      // 一个像素对应多少世界 x
    float sx = size.x / spanx;      // 像素/世界x
    float sy = size.y / spany;      // 像素/世界y

    float f0 = F(x);
    float d1 = DF;

    // ---- 1) 平滑描边：屏幕空间的一阶距离（陡峭段等宽 + 抗锯齿）----
    float slope = d1 * sy / sx;
    float dpx = abs(f0 - y) * sy / sqrt(1.0 + slope * slope);
    float cov = clamp(lineWidth * 0.5 + 0.5 - dpx, 0.0, 1.0);

    // ---- 2) 欠采样列 -> 画 ± 包络带（而不是随机锯齿）----
    // 在一个像素的 x 区间里采 8 个点，用两把互补的尺子判断"这一列画不下"：
    //   a) 折返次数：列内曲线上下折返 >= 2 次 => 一列里塞进了多个振荡；
    //      （只折返 1 次 = 可分辨的极值，照常用描边画线）
    //   b) 实测跨度 spread 远小于导数预期 |f'|·dx => 深处的欠采样
    //      （振子快到 8 个采样都抓不住规律时，a 会受相位噪声影响，b 来兜底）
    // 此时把 [min,max] 填成实心带：对 sin(1/x) 就等于填它的真实 ±1 包络。
    if (abs(d1) * dx * sy > 1.0) {
        float h = 0.5 * dx;
        float s0 = F(x - h);
        float s1 = F(x - 0.75 * h);
        float s2 = F(x - 0.5 * h);
        float s3 = F(x - 0.25 * h);
        float s4 = F(x + 0.25 * h);
        float s5 = F(x + 0.5 * h);
        float s6 = F(x + 0.75 * h);
        float s7 = F(x + h);
        s0 = (s0 != s0) ? 0.0 : s0;     // NaN 采样点当 0（不参与 min/max）
        s1 = (s1 != s1) ? 0.0 : s1;
        s2 = (s2 != s2) ? 0.0 : s2;
        s3 = (s3 != s3) ? 0.0 : s3;
        s4 = (s4 != s4) ? 0.0 : s4;
        s5 = (s5 != s5) ? 0.0 : s5;
        s6 = (s6 != s6) ? 0.0 : s6;
        s7 = (s7 != s7) ? 0.0 : s7;

        float yl = min(min(min(s0, s1), min(s2, s3)), min(min(s4, s5), min(s6, s7)));
        float yh = max(max(max(s0, s1), max(s2, s3)), max(max(s4, s5), max(s6, s7)));
        float spread = (yh - yl) * sy;                  // 实测包络跨度（像素）

        float d0 = s1 - s0;
        float d1i = s2 - s1;
        float d2 = s3 - s2;
        float d3 = s4 - s3;
        float d4 = s5 - s4;
        float d5 = s6 - s5;
        float d6 = s7 - s6;
        float turns = 0.0;
        if (d0 * d1i < 0.0) { turns += 1.0; }
        if (d1i * d2 < 0.0) { turns += 1.0; }
        if (d2 * d3 < 0.0) { turns += 1.0; }
        if (d3 * d4 < 0.0) { turns += 1.0; }
        if (d4 * d5 < 0.0) { turns += 1.0; }
        if (d5 * d6 < 0.0) { turns += 1.0; }

        float pred = max(abs(d1) * dx * sy, 2.0);       // 导数预期跨度（像素）
        float w = max(step(2.0, turns),                 // a) 列内折返 >= 2 次
                      1.0 - smoothstep(0.15, 0.5, spread / pred));  // b) 深欠采样
        w *= step(spread, 4.0 * size.y);                // 极点/真跳变：不填（否则整列涂满）
        w *= smoothstep(1.5, 4.0, spread);              // 带不足 1.5 像素就没必要填

        float band = clamp((yh - y) * sy + 0.5, 0.0, 1.0)
                   * clamp((y - yl) * sy + 0.5, 0.0, 1.0);
        cov = mix(cov, max(cov, band), w);
    }

    float a = cov * color.a * qt_Opacity;
    if (isnan(f0)) {
        a = 0.0;                       // sin(1/0) 之类的点直接丢弃
    }
    fragColor = vec4(color.rgb * a, a);   // 预乘 alpha
}
"""


def shader_sources(expr: sp.Expr) -> tuple[str, str]:
    """QML 前端（隐式模型）的顶点/片元着色器源码。"""
    return (
        VERTEX_SHADER,
        FRAGMENT_TEMPLATE.replace("@FUNC@", func_glsl(expr, "u"))
        .replace("@DFUNC@", dfunc_glsl(expr)),
    )
