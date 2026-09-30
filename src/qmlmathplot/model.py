"""Model 层：表达式 -> GLSL、着色器源码模板、视图矩形（平移/缩放）数学。

这一层不 import 任何 Qt，可以脱离 GUI 单独测试；ViewModel（PlotController）
和 View（QML 的 ShaderEffect）都建立在它上面。

绘图模型是**隐式（signed-distance）**：片元着色器逐像素算"到曲线的一阶屏幕
空间距离"，每帧代价 ∝ 像素数、与函数频率无关（振荡函数不会因为混叠把帧率拖
垮）；一个像素里塞进多个振荡的列改填 [min,max] 包络带 —— 见 FRAGMENT_TEMPLATE
上面的注释。
"""

from __future__ import annotations

import re

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
    """GLSL printer 的补丁：

    1. 小整数次幂不生成 pow()。GLSL 的 pow(x, y) 在 x < 0 时未定义（不少驱动直接
       给 NaN），而 sympy 会把 x**2 印成 pow(x, 2.0)。这里改成连乘，顺带更快。
    2. 可选把某个符号印成 ``(name)``：表达式会内联进 ``#define F(u) (...)``，
       宏是**文本替换**，``1.0/u`` 遇到 ``F(x - h)`` 会变成 ``1.0/x - h``（少一层
       括号就改了语义）。实测这个括号缺失让 8 个采样点全算成 ``1.0/x - h_k``，
       包络判据因此永远拿不到有效采样。
    """

    def __init__(self, wrap_symbol: str | None = None) -> None:
        super().__init__()
        self._wrap_symbol = wrap_symbol

    def _print_Symbol(self, expr: sp.Symbol) -> str:
        text = super()._print_Symbol(expr)
        return f"({text})" if expr.name == self._wrap_symbol else text

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


# GPU 的 sin/cos 在参数很大时不可靠（实测 Intel D3D11：sin(1/x) 的 8 个采样点几乎
# 相同、cos(1/x) 返回近 0 的垃圾），而 sin(1/x) 这类函数在奇点附近参数能到几百上千。
# 先把实参折进 [0, 2π) 再调内置函数，小参数上各家实现都是准的。
_SIN_COS = re.compile(r"(?<![A-Za-z0-9_])(sin|cos)\(")


def _wrap_trig(text: str) -> str:
    return _SIN_COS.sub(lambda m: ("SIN(" if m.group(1) == "sin" else "COS("), text)


def func_glsl(expr: sp.Expr, name: str = "x", wrap: bool = False) -> str:
    """把 sympy 表达式印成 GLSL 表达式（返回 f(x) 的右值文本）。

    name 用于改名：着色器里 ``#define F(u) (...)`` 要把变量印成 u。
    wrap=True 时把该变量印成 ``(u)``，供宏文本替换安全使用（见 _PlotGLSLPrinter）。
    sin/cos 会被包成模板里的 SIN/COS（带参数归约，见模板注释）。
    """
    if name != "x":
        expr = expr.subs(sp.Symbol("x"), sp.Symbol(name))
    printer = _PlotGLSLPrinter(wrap_symbol=name if wrap else None)
    return _wrap_trig(printer.doprint(expr))


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

// 参数归约：GPU 的内置 sin/cos 在大参数上不可靠，折进 [0,2π) 再调用
#define TAU 6.283185307179586
float _wrap(float t) { return t - TAU * floor(t * (1.0 / TAU)); }
#define SIN(t) sin(_wrap(t))
#define COS(t) cos(_wrap(t))

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
    // 在 ±2 列的窗口里采 16 个点，用两把互补的尺子判断"这一列画不下"：
    //   a) 折返次数：列内曲线上下折返 >= 2 次 => 一列里塞进了多个振荡；
    //      （只折返 1 次 = 可分辨的极值，照常用描边画线）
    //   b) 实测跨度 spread 远小于导数预期 |f'|·dx => 深处的欠采样
    //      （振子快到采样都抓不住规律时，a 会受相位噪声影响，b 来兜底）
    // 此时把 [min,max] 填成实心带：对 sin(1/x) 就等于填它的真实 ±1 包络。
    if (abs(d1) * dx * sy > 1.0) {
        // 两把尺子分开用，避免"为了消锯齿而把填充范围撑太宽"：
        //  * 窄窗口（正好一列、8 点）判"这一列画不下"——决定填哪些列。判据只看本列，
        //    填充范围才不会被邻域撑宽（实测 ±1 列时 15 列、只本列时见下）。代价是
        //    单列采样对振荡函数有相位噪声，边界列可能虚实交替；内部漏判由下面的
        //    min(cov, lim) 兜底，不会留缝。
        //  * 宽窗口（±8 列、16 点）只用来估带的上/下边缘——窗口宽，采样点才更可能
        //    碰到极值，带的边缘才不会因包络偏窄而出现暗缝/台阶。
        float h = 0.5 * dx;
        float nl = 1e30;
        float nh = -1e30;
        float turns = 0.0;
        float prev_s = 0.0;
        float prev_d = 0.0;
        for (int i = 0; i < 8; i++) {
            float t = -1.0 + 2.0 * (float(i) + 0.5) / 8.0;    // 正好一列
            float s = F(x + t * h);
            s = (s != s) ? 0.0 : s;         // NaN 采样点当 0（不参与 min/max）
            nl = min(nl, s);
            nh = max(nh, s);
            if (i > 0) {
                float d = s - prev_s;
                if (prev_d * d < 0.0) { turns += 1.0; }       // 列内折返
                prev_d = d;
            }
            prev_s = s;
        }
        // 32 个点：16 个点时最大值的期望只有 ~0.95×振幅、逐列抖动，带的边缘会有
        // 5~20px 的缺齿；加密到 32 点后包络更贴近真实值域，边缘才平。
        float wl = 1e30;
        float wh = -1e30;
        for (int i = 0; i < 32; i++) {
            float t = -16.0 + 32.0 * (float(i) + 0.5) / 32.0;  // ±8 列
            float s = F(x + t * h);
            s = (s != s) ? 0.0 : s;
            wl = min(wl, s);
            wh = max(wh, s);
        }
        float spread = (nh - nl) * sy;                  // 实测包络跨度（像素，窄窗口）

        float pred = max(abs(d1) * dx * sy, 2.0);       // 导数预期跨度（像素）
        float w = max(step(2.0, turns),                 // a) 列内折返 >= 2 次
                      1.0 - smoothstep(0.15, 0.5, spread / pred));  // b) 深欠采样
        w *= step(spread, 4.0 * size.y);                // 极点/真跳变：不填（否则整列涂满）
        w *= smoothstep(1.5, 4.0, spread);              // 带不足 1.5 像素就没必要填

        // 带的上下边缘取宽窗口的包络（窄窗口会咬出暗缝）
        float band = clamp((wh - y) * sy + 2.0, 0.0, 1.0)
                   * clamp((y - wl) * sy + 2.0, 0.0, 1.0);
        // 描边在这里不可信：|f'| 极大时切线近似对任何 y 都算得极小的"水平距离"，
        // cov 会饱和成整列，而且切线外推会涂到远超真实值域的地方（实测 sin(1/x)
        // 被涂到 ±1.8，真实值域是 ±1）。所以：
        //   1) 先用采样包络钳住描边（包络是这一列里 f 实测到过的范围）；
        //   2) 有置信度(w>0)的列整列换成包络带。硬切不产生接缝：gate 边界上
        //      包络宽度 ≈ 描边宽度。
        // 描边钳制用**窄窗口**包络（这一列自己采到的范围）：于是"实心块"只出现在
        // 真正不可分辨的列（w>0），外面恢复成曲线自己的陡峭段——否则切线外推会把
        // 填充范围一路撑宽（实测 15 列，理论不可分辨区只有 ~7 列）。
        // 端头留 4px 收细（羽化），不是方形硬切。
        float lim = clamp((nh - y) * sy + 4.0, 0.0, 1.0)
                  * clamp((y - nl) * sy + 4.0, 0.0, 1.0);
        // 与带做**平滑**混合：硬切会让边界列忽带忽线；w=0 的列由 min(cov, lim) 兜底。
        cov = mix(min(cov, lim), band, w);
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
        FRAGMENT_TEMPLATE.replace("@FUNC@", func_glsl(expr, "u", wrap=True))
        .replace("@DFUNC@", dfunc_glsl(expr)),
    )
