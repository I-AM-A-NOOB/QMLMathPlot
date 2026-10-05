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


def _pole_factors(expr: sp.Expr) -> list[sp.Expr]:
    """收集"极点分母"：这些因子变号的地方就是函数从 +∞ 跳到 -∞ 的地方。

    只收会**变号**的因子（x、cos(x)…）：1/x² 这种不变号的极点不需要断口——
    两侧都趋向 +∞，画出来本来就自然相连。
    """
    out: list[sp.Expr] = []
    if isinstance(expr, sp.Pow) and expr.exp.is_number and expr.exp.is_negative:
        out.append(expr.base)
    if isinstance(expr, sp.tan | sp.sec):
        out.append(sp.cos(expr.args[0]))
    elif isinstance(expr, sp.cot | sp.csc):
        out.append(sp.sin(expr.args[0]))
    for arg in expr.args:
        out.extend(_pole_factors(arg))
    return out


def _domain_conditions(expr: sp.Expr) -> list[sp.Expr]:
    """收集定义域条件（sympy 表达式，需恒为真才算在定义域内）。

    覆盖常见的域边界：分母不为 0、log 的实参 > 0、sqrt 的实参 >= 0、
    asin/acos 的实参 ∈ [-1,1]、tan/sec 的 cos != 0、cot/csc 的 sin != 0。
    """
    out: list[sp.Expr] = []
    if isinstance(expr, sp.Pow) and expr.exp.is_number and expr.exp.is_negative:
        out.append(sp.Ne(expr.base, 0))
    elif isinstance(expr, sp.Pow) and expr.exp == sp.Rational(1, 2):
        out.append(sp.Ge(expr.base, 0))
    if isinstance(expr, sp.log):
        out.append(sp.Gt(expr.args[0], 0))
    elif isinstance(expr, sp.asin | sp.acos):
        out.append(sp.And(sp.Ge(expr.args[0], -1), sp.Le(expr.args[0], 1)))
    elif isinstance(expr, sp.tan | sp.sec):
        out.append(sp.Ne(sp.cos(expr.args[0]), 0))
    elif isinstance(expr, sp.cot | sp.csc):
        out.append(sp.Ne(sp.sin(expr.args[0]), 0))
    for arg in expr.args:
        out.extend(_domain_conditions(arg))
    return out


def unbounded_edges(expr: sp.Expr, name: str = "x") -> list[tuple[float, bool, bool]]:
    """定义域边界上"函数确实趋于 ±∞"的点：[(边界x, 趋于-∞?, 趋于+∞?)]。

    只有**慢发散**才需要它：log(x) 在 x→0+ 趋于 -∞，但固定宽度的采样窗口永远
    采不到足够深的值（最左有效采样只给到 log≈-7），深视口里曲线会整段消失。
    1/x、tan 发散快，采样值天然超出任何视口，不需要（而且 tan 的极点集是无限的，
    解析上也枚举不完）。
    """
    sym = sp.Symbol(name)
    out: list[tuple[float, bool, bool]] = []
    for cond in _domain_conditions(expr):
        lhs = cond.lhs if isinstance(cond, (sp.Gt, sp.Ge, sp.Ne)) else None
        if lhs is None or sym not in lhs.free_symbols:
            continue
        try:
            points = sp.solve(sp.Eq(lhs, 0), sym)
        except Exception:  # noqa: BLE001 —— 解不出边界就放弃（退回纯采样）
            continue
        for point in points:
            if not point.is_number:
                continue
            down = up = False
            for side in ("+", "-"):
                try:
                    lim = sp.limit(expr, sym, point, side)
                except Exception:  # noqa: BLE001
                    continue
                if lim is sp.oo:
                    up = True
                elif lim is -sp.oo:
                    down = True
            if down or up:
                out.append((float(point), down, up))
    return sorted(set(out))


def _edge_glsl(expr: sp.Expr) -> tuple[str, str]:
    """生成两个 GLSL 片段：边界命中查询、边界方向查询（无边界时都为空）。"""
    edges = unbounded_edges(expr)
    if not edges:
        return "", ""
    hit = "\n".join(
        f"    if ((xa <= {x!r}) && ({x!r} <= xb)"
        f" && abs({x!r} - 0.5 * (xa + xb)) < abs(best - 0.5 * (xa + xb))) best = {x!r};"
        for x, _d, _u in edges
    )
    du = "\n".join(
        f"    if (abs(ex - {x!r}) < 1e-9) return vec2({-1.0 if d else 0.0}, {1.0 if u else 0.0});"
        for x, d, u in edges
    )
    return hit, du


def pole_glsl(expr: sp.Expr, name: str = "x") -> str | None:
    """极点分母的乘积（GLSL）。在区间两端求值、乘积 <= 0 即跨过极点。"""
    factors = _pole_factors(expr)
    if not factors:
        return None
    return " * ".join(f"({func_glsl(f, name)})" for f in factors)


def domain_glsl(expr: sp.Expr, name: str = "x") -> str | None:
    """定义域谓词（GLSL 关系表达式）。无法分析时返回 None。"""
    conds = _domain_conditions(expr)
    if not conds:
        return None
    return " && ".join(f"({func_glsl(c, name)})" for c in conds)


def dfunc_glsl(expr: sp.Expr, name: str = "x", wrap: bool = False) -> str:
    """f'(x) 的 GLSL 文本。wrap 语义同 func_glsl。"""
    return func_glsl(sp.diff(expr, sp.Symbol("x")), name, wrap=wrap)


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

// 点到线段的距离（屏幕空间）。按线段描边 => 笔触处处等宽。
float _seg_dist(vec2 p, vec2 a, vec2 b) {
    vec2 ab = b - a;
    float t = clamp(dot(p - a, ab) / max(dot(ab, ab), 1e-12), 0.0, 1.0);
    return length(p - (a + t * ab));
}

// 无界定义域边界（sympy 单侧极限注入）：log(x) 在 x→0+ 这类慢发散边界。
// 固定宽度采样窗口采不到足够深的值，深视口里曲线会整段消失；这里把折线沿边界
// 延伸成竖直射线到 ±1e12，任意深度都能画到，且射线严格落在边界 x 上。
float _edge_hit(float xa, float xb) {
    float best = 1e30;
@EDGEHIT@
    return best;
}
vec2 _edge_du(float ex) {
@EDGEDU@
    return vec2(0.0, 0.0);
}

#define F(u) (@FUNC@)
#define DF @DFUNC@
#define DFP_(u) (@DFUNC_U@)
// 定义域谓词（域外像素不画）与"极点分母"（变号 => 该区间跨过极点）
@DOM@
@POLE@

void main() {
    float x = mix(view.x, view.y, vUV.x);
    float y = mix(view.w, view.z, vUV.y);

    // 定义域：域外像素不画（log(x) 的 x<0、sqrt(x) 的 x<0、asin 的 |x|>1 …）
    if (!DOM(x)) {
        fragColor = vec4(0.0);
        return;
    }

    float spanx = view.y - view.x;
    float spany = view.w - view.z;
    float dx = spanx / size.x;      // 一个像素对应多少世界 x
    float sx = size.x / spanx;      // 像素/世界x
    float sy = size.y / spany;      // 像素/世界y

    float f0 = F(x);
    float d1 = DF;

    // ---- 1) 采样 + 折线描边 ----
    // Desmos 的做法（engineering.desmos.com）：把采样点连成**线段**、按线段描边，
    // 相邻点提示跳变就断开。这样笔触处处等宽；而"算到切线的距离"在陡峭/强弯曲处
    // 会高估距离，线会变细甚至渐变消失（log(x) 在 x→0+、sin(1/x) 的陡段）。
    float h = 0.5 * dx;
    float nl = 1e30;
    float nh = -1e30;
    float turns = 0.0;
    float prev_s = 0.0;
    float prev_x = 0.0;
    float prev_d = 0.0;
    bool prev_ok = false;
    vec2 p = vec2(x * sx, y * sy);          // 屏幕空间（平移不影响距离）
    float dmin = 1e30;
    // 16 个点（±1 列，间距 0.125 列）：折返次数要用来判"亚像素振荡"，8 个点时
    // 随机采样有 ~11% 的列会数出 <2 次（漏判），残留竖条；16 点降到 ~0.05%。
    for (int i = 0; i < 16; i++) {
        float t = -1.0 + 2.0 * (float(i) + 0.5) / 16.0;     // ±1 列
        float xi = x + t * h;
        float s = F(xi);
        // 域外/NaN/inf（极点）跳过：既不参与包络，也不连线段。带 DOM 是因为
        // GLSL 的 log(负) 等是 undefined，个别驱动会返回有限垃圾值。
        bool ok = DOM(xi) && (s == s) && (abs(s) < 1e30);
        if (ok) {
            nl = min(nl, s);
            nh = max(nh, s);
            if (prev_ok) {
                float d = s - prev_s;
                if (prev_d * d < 0.0) { turns += 1.0; }      // 列内折返（折线方向变化）
                prev_d = d;
                {
                    // 说明：这里不再用"落差 > 4 个视口高就断线"的跳变判据。它是**像素/
                    // 视口相对量**，放大后会把合法的陡峭段也切断（实测中等放大档的
                    // 竖条因此断续）；而极点已由解析断口（分母变号、世界单位）单独
                    // 负责，域外采样也已被 ok 过滤掉，不需要再猜。
                    // 折返段（本列含一个极值）：采样点不一定落在峰顶，折线会把峰/谷
                    // 削平（sin(150*x) 每周期仅 ~12 个采样点、sin(1/x) 亦然）。这里对
                    // 该段做两步二分把驻点逼近到 1/4 段内，插入真正的极值顶点。
                    // Desmos 同法："相邻点提示极值就二分"。（全段都二分太贵，实测收益
                    // ~1px；只在折返段做，成本可控。）
                    float da = DFP_(prev_x);
                    float db = DFP_(xi);
                    if (da == da && db == db && da * db < 0.0) {
                        float lo = prev_x, hi = xi, dlo = da;
                        for (int k = 0; k < 2; k++) {
                            float mid = 0.5 * (lo + hi);
                            float dm = DFP_(mid);
                            if (dm == dm && dlo * dm <= 0.0) { hi = mid; }
                            else { lo = mid; dlo = dm; }
                        }
                        float xs_ = 0.5 * (lo + hi);
                        float ys_ = F(xs_);
                        if (ys_ == ys_ && abs(ys_) < 1e30) {
                            vec2 apex = vec2(xs_ * sx, ys_ * sy);
                            dmin = min(dmin, _seg_dist(p, vec2(prev_x * sx, prev_s * sy), apex));
                            dmin = min(dmin, _seg_dist(p, apex, vec2(xi * sx, s * sy)));
                            continue;   // 已用带极值的两段，跳过下文的单段
                        }
                    }
                    dmin = min(dmin, _seg_dist(p, vec2(prev_x * sx, prev_s * sy),
                                                  vec2(xi * sx, s * sy)));
                }
            }
            prev_x = xi;
            prev_s = s;
        }
        prev_ok = ok;
    }
    // ---- 1b) 无界边界上的无穷延伸 ----
    // 窗口跨过"函数趋于 ±∞"的定义域边界时，把折线从最近的有效采样沿边界拉一条
    // 竖直射线到 ±1e12：慢发散函数（log(x) 在 x→0+）的下降段因此能画到任意深度。
    // 射线落在边界 x 上（不是各列自己的采样 x），所以线宽仍然恒定、各列对齐。
    float ex_ = _edge_hit(x - h, x + h);
    if (ex_ < 1e29 && nl < 1e29) {
        vec2 du_ = _edge_du(ex_);
        if (du_.x != 0.0) {
            dmin = min(dmin, _seg_dist(p, vec2(ex_ * sx, nl * sy), vec2(ex_ * sx, -1e12)));
        }
        if (du_.y != 0.0) {
            dmin = min(dmin, _seg_dist(p, vec2(ex_ * sx, nh * sy), vec2(ex_ * sx, 1e12)));
        }
    }
    float cov = clamp(lineWidth * 0.5 + 0.5 - dmin, 0.0, 1.0);

    // ---- 2) 欠采样列 -> 画 ± 包络带（而不是随机锯齿）----
    // 在 ±2 列的窗口里采 16 个点，用两把互补的尺子判断"这一列画不下"：
    //   a) 折返次数：列内曲线上下折返 >= 2 次 => 一列里塞进了多个振荡；
    //      （只折返 1 次 = 可分辨的极值，照常用描边画线）
    //   b) 实测跨度 spread 远小于导数预期 |f'|·dx => 深处的欠采样
    //      （振子快到采样都抓不住规律时，a 会受相位噪声影响，b 来兜底）
    // 此时把 [min,max] 填成实心带：对 sin(1/x) 就等于填它的真实 ±1 包络。
    if (abs(d1) * dx * sy > 1.0) {
        // 宽窗口（±8 列、16 点）只用来估带的上/下边缘——窗口宽，采样点才更可能
        // 碰到极值，带的边缘才不会因包络偏窄而出现暗缝/台阶。
        float wl = 1e30;
        float wh = -1e30;
        for (int i = 0; i < 16; i++) {
            float t = -16.0 + 32.0 * (float(i) + 0.5) / 16.0;  // ±8 列
            float s = F(x + t * h);
            if (s == s && abs(s) < 1e30) {
                wl = min(wl, s);
                wh = max(wh, s);
            }
        }
        float spread = (nh - nl) * sy;                  // 实测包络跨度（像素，窄窗口）

        // 真跳变：这一列跨过极点（分母变号）且函数值远超视口 => +∞/-∞ 的连线，
        // 不画（消除 1/x、tan(x) 在渐近线处的竖直连线）。sin(1/x) 这类**有界**的
        // 振荡值不会超视口，照旧由包络带表示。
        // 只对"本列跨过极点"的列留断口（断口对齐极点，分支照常画到渐近线附近）
        float _hp = 0.5 * dx;
        // 判据用"采样跨度 > 32 倍视口高度"而不是像素数：像素阈值会随缩放漂移
        // （深缩放时 sin(1/x) 的有界振荡也会超过 8 个视口高，从而被误切一刀）。
        if (POLE(x - _hp) * POLE(x + _hp) <= 0.0 && (nh - nl) > 32.0 * spany) {
            cov = 0.0;
        } else {

        float pred = max(abs(d1) * dx * sy, 2.0);       // 导数预期跨度（像素）
        float w = step(2.0, turns);                     // a) 列内折返 >= 2 次
        // d) 亚像素振荡（不依赖采样的随机性）：一列内曲线按导数要走的距离 pred 远超它
        //    实测的值域跨度 spread —— 振荡周期远小于一列，采样必然漏判（16 点也可能
        //    恰好呈单调）。阈值 16 是量出来的：log 这类单调凹曲线该比值 ~3.6、tan/1/x
        //    的分支 <1，真正亚像素的 sin(1/x) 到 ~58。pred >= 4px 的附加约束排除
        //    "可分辨极值"（峰顶 pred≈0、spread≈0，比值会误判）。
        w = max(w, step(16.0 * spread + 1e-6, pred) * step(4.0, pred));
        // 两个历史坑：
        //  * 判据 (b)"实测跨度 ≪ 导数预期"曾用 smoothstep(0.15,0.5,…) 软阈值，误伤
        //    单调陡段（log 比值 ~0.28 → 被整块填充）与可分辨极值。(d) 改用硬阈值
        //    1/16 且要求 pred>=4px，才把两者分开。
        //  * 闸门 step(spread, 4*size.y) 是**像素单位**：放大到视口跨度 < 0.5 时
        //    sin(1/x) 的值域 ±1 换算成像素就超过 4 个视口高，带被整个关掉 → 满屏
        //    竖条。极点改由解析断口（世界单位、分母变号）负责，不再用像素跨度猜。
        w *= smoothstep(1.5, 4.0, spread);              // 带不足 1.5 像素就没必要填


        // 带的上下边缘取宽窗口的包络（窄窗口会咬出暗缝）
        float band = clamp((wh - y) * sy + 2.0, 0.0, 1.0)
                   * clamp((y - wl) * sy + 2.0, 0.0, 1.0);
        // 触发列（一列里塞进多个振荡）整列换成包络带：折线在那种列里是随机锯齿，
        // 包络带才是它真实的取值范围。w=0 的列保持折线描边（等宽）。
        cov = mix(cov, band, w);
        }
    }

    float a = cov * color.a * qt_Opacity;
    if (isnan(f0) || isinf(f0)) {
        a = 0.0;                       // sin(1/0)、1/0 之类的点直接丢弃
    }
    fragColor = vec4(color.rgb * a, a);   // 预乘 alpha
}
"""


def shader_sources(expr: sp.Expr) -> tuple[str, str]:
    """QML 前端（隐式模型）的顶点/片元着色器源码。"""
    frag = (
        FRAGMENT_TEMPLATE.replace("@FUNC@", func_glsl(expr, "u", wrap=True))
        .replace("@DFUNC@", dfunc_glsl(expr))
        .replace("@DFUNC_U@", dfunc_glsl(expr, "u", wrap=True))
    )
    edge_hit, edge_du = _edge_glsl(expr)
    frag = frag.replace("@EDGEHIT@", edge_hit).replace("@EDGEDU@", edge_du)
    # 定义域 / 极点：宏参数带括号，和 F 同理（宏是文本替换）
    dom = domain_glsl(expr, "u")
    frag = frag.replace(
        "@DOM@",
        f"#define DOM(u) ({dom})" if dom else "#define DOM(u) true",
    )
    pole = pole_glsl(expr, "u")
    frag = frag.replace(
        "@POLE@",
        f"#define POLE(u) ({pole})" if pole else "#define POLE(u) 1.0",
    )
    return (VERTEX_SHADER, frag)
