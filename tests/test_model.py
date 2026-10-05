"""Model 层单测：表达式 -> GLSL、着色器源码、视图矩形数学（不依赖 Qt）。"""

import pytest
import sympy as sp

from qmlmathplot import model

X = sp.Symbol("x")


@pytest.mark.parametrize(("expr", "expected"), [
    (X**2, "(x*x)"),
    (X**-2, "(1.0/(x*x))"),
    (X**9, "pow(x, 9.0)"),
    (sp.sin(X) / X, "SIN(x)/x"),
    (sp.exp(-X * X) * sp.sin(10 * X), "exp(-(x*x))*SIN(10*x)"),
])
def test_func_glsl(expr, expected):
    """小整数次幂必须连乘（GLSL 的 pow(x, y) 在 x<0 时未定义，驱动常给 NaN）；
    sin/cos 必须包成 SIN/COS（参数归约，见 model 里的说明）。"""
    assert model.func_glsl(expr) == expected


def test_func_glsl_renames_variable():
    assert model.func_glsl(sp.sin(1 / X), "u") == "SIN(1.0/u)"


def test_dfunc_glsl():
    assert model.dfunc_glsl(sp.sin(1 / X)) == "-COS(1.0/x)/(x*x)"


def test_func_glsl_wraps_macro_parameter():
    """wrap=True 把宏参数印成 (u)：宏是文本替换，少这层括号就改了语义。"""
    assert model.func_glsl(sp.sin(1 / X), "u", wrap=True) == "SIN(1.0/(u))"


def test_shader_sources_inject_expression():
    vert, frag = model.shader_sources(sp.sin(1 / X))
    assert vert == model.VERTEX_SHADER
    assert "@FUNC@" not in frag
    assert "@DFUNC@" not in frag
    # 宏是文本替换：参数必须带括号，否则 F(x - h) 会展开成 1.0/x - h
    # （实测这个括号缺失让 8 个采样点全算错，包络判据永远拿不到有效采样）
    assert "#define F(u) (SIN(1.0/(u)))" in frag
    assert "#define DF -COS(1.0/x)/(x*x)" in frag  # 导数仍按 x 求值


def test_zoom_round_trip_is_exact():
    rect = model.ViewRect()
    before = rect.as_tuple()
    rect.zoom(120, 0.3, 0.4)
    assert rect.as_tuple() != before
    rect.zoom(-120, 0.3, 0.4)
    assert rect.as_tuple() == pytest.approx(before)


def test_zoom_keeps_anchor_world_point():
    rect = model.ViewRect()
    anchor = rect.world_at(0.25, 0.75)
    rect.zoom(120, 0.25, 0.75)
    assert rect.world_at(0.25, 0.75) == pytest.approx(anchor)


def test_pan_pixels_moves_by_view_fraction():
    rect = model.ViewRect()
    rect.pan_pixels(90, 60, 900, 600)  # 宽、高各 1/10
    assert rect.as_tuple() == pytest.approx((-7.2, 4.8, -1.6, 2.4))


def test_pan_ignores_zero_size():
    rect = model.ViewRect()
    before = rect.as_tuple()
    rect.pan_pixels(10, 10, 0, 0)
    assert rect.as_tuple() == before


def test_reset_restores_default_view():
    rect = model.ViewRect()
    rect.zoom(240, 0.5, 0.5)
    rect.reset()
    assert rect.as_tuple() == model.DEFAULT_VIEW


def test_pole_and_domain_analysis():
    """极点因子（变号处 = +∞/-∞ 跳变）与定义域条件。"""
    assert model.pole_glsl(1 / X) == "(x)"
    assert model.domain_glsl(1 / X) == "(x != 0)"
    assert model.pole_glsl(sp.tan(X)) == "(COS(x))"      # tan 的极点在 cos(x)=0
    assert model.domain_glsl(sp.tan(X)) == "(COS(x) != 0)"
    assert model.domain_glsl(sp.log(X)) == "(x > 0)"
    assert model.domain_glsl(sp.sqrt(X)) == "(x >= 0)"
    assert model.pole_glsl(X**2) is None                 # 处处连续，没有极点因子
    assert model.domain_glsl(X**2) is None               # 全定义域


def test_shader_sources_inject_domain_and_pole():
    _, frag = model.shader_sources(1 / X)
    assert "#define DOM(u) ((u != 0))" in frag
    assert "#define POLE(u) ((u))" in frag
    _, smooth = model.shader_sources(X**2)
    assert "#define DOM(u) true" in smooth               # 无法分析 => 恒真
    assert "#define POLE(u) 1.0" in smooth               # 无极点 => 乘积恒正，不会误判




def test_unbounded_edges():
    """哪些定义域边界需要"无穷延伸"：只有慢发散的那些。

    log(x) 在 x→0+ 趋于 -∞，但采样窗口固定宽度、采不到那么深 → 深视口里曲线会整段
    消失，必须靠解析出的边界射线补上。sqrt(x) 在 0 有界（0），sin(1/x) 在 0 无极限，
    都不需要；1/x 快发散，采样天然够深，但解析上标出双方向也无害。
    """
    assert model.unbounded_edges(sp.log(X)) == [(0.0, True, False)]
    assert model.unbounded_edges(sp.sqrt(X)) == []
    assert model.unbounded_edges(sp.sin(1 / X)) == []
    assert model.unbounded_edges(X**2) == []
    assert sorted(model.unbounded_edges(1 / X)) == [(0.0, True, True)]
    assert model.unbounded_edges(1 / X**2) == [(0.0, False, True)]
