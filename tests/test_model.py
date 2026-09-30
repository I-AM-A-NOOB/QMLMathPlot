"""Model 层单测：表达式 -> GLSL、着色器源码、视图矩形数学（不依赖 Qt）。"""

import pytest
import sympy as sp

from qmlmathplot import model

X = sp.Symbol("x")


@pytest.mark.parametrize(("expr", "expected"), [
    (X**2, "(x*x)"),
    (X**-2, "(1.0/(x*x))"),
    (X**9, "pow(x, 9.0)"),
    (sp.sin(X) / X, "sin(x)/x"),
    (sp.exp(-X * X) * sp.sin(10 * X), "exp(-(x*x))*sin(10*x)"),
])
def test_func_glsl(expr, expected):
    """小整数次幂必须连乘：GLSL 的 pow(x, y) 在 x<0 时未定义（驱动常给 NaN）。"""
    assert model.func_glsl(expr) == expected


def test_func_glsl_renames_variable():
    assert model.func_glsl(sp.sin(1 / X), "u") == "sin(1.0/u)"


def test_dfunc_glsl():
    assert model.dfunc_glsl(sp.sin(1 / X)) == "-cos(1.0/x)/(x*x)"


def test_shader_sources_inject_expression():
    vert, frag = model.shader_sources(sp.sin(1 / X))
    assert vert == model.VERTEX_SHADER
    assert "@FUNC@" not in frag
    assert "@DFUNC@" not in frag
    assert "#define F(u) (sin(1.0/u))" in frag  # 宏里变量改名成 u
    assert "#define DF -cos(1.0/x)/(x*x)" in frag  # 导数仍按 x 求值


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
