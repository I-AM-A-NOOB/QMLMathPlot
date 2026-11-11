"""Shader resources: external files, one assembler, no unresolved placeholders."""

from __future__ import annotations

import pytest
import sympy as sp

from qmlmathplot.calc.ggb_compute import path_compute_source
from qmlmathplot.calc.model import implicit_shader_sources
from qmlmathplot.shaderbuild import render, shader_source

SHADERS = (
    "fullscreen.vert",
    "path.vert",
    "path.frag",
    "path_state.glsl",
    "interval_path.comp",
    "implicit.frag",
    "interval_lib.glsl",
    "common/uniforms.glsl",
    "common/arg_reduction.glsl",
    "common/seg_dist.glsl",
    "common/span_cov.glsl",
    "common/real_pow.glsl",
)


def test_every_shader_resource_loads() -> None:
    for name in SHADERS:
        assert shader_source(name).strip(), name


def test_render_refuses_unresolved_placeholders() -> None:
    """A missing substitution used to surface only as a cryptic qsb parse error."""
    with pytest.raises(ValueError, match="unresolved shader placeholders"):
        render("implicit.frag")


def test_shared_blocks_are_includes() -> None:
    """The uniform block and stroke helpers have one definition."""
    assert "@INCLUDE:common/" in shader_source("implicit.frag")
    _, fragment = implicit_shader_sources(sp.sympify("x**2 + y**2 - 1"))
    assert "@INCLUDE:" not in fragment
    assert "#define TAU" in fragment
    assert "uniform buf {" in fragment


def test_assembled_sources_are_complete() -> None:
    for expression in ("sin(x)", "tan(x)", "exp(tan(x))"):
        assert "@" not in path_compute_source(expression), expression
    for expression in ("x**2 + y**2 - 1", "x - tan(y)"):
        _, fragment = implicit_shader_sources(sp.sympify(expression))
        assert "@" not in fragment, expression
        assert "#define F(" in fragment
