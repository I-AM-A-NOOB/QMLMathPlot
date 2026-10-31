"""Shader resources: external files, one assembler, no unresolved placeholders."""

from __future__ import annotations

import pytest
import sympy as sp

from qmlmathplot.ggb_compute import path_compute_source
from qmlmathplot.model import shader_sources
from qmlmathplot.shaderbuild import render, shader_source

SHADERS = (
    "fullscreen.vert",
    "path.vert",
    "path.frag",
    "interval_path.comp",
    "implicit.frag",
    "oned_sampled.frag",
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
        render("oned_sampled.frag")


def test_shared_blocks_are_includes() -> None:
    """The uniform block, argument reduction and stroke helpers have one definition."""
    assert "@INCLUDE:common/" in shader_source("oned_sampled.frag")
    _, fragment = shader_sources(sp.sympify("sin(x)"))
    assert "@INCLUDE:" not in fragment
    assert "#define TAU" in fragment          # expanded
    assert "uniform buf {" in fragment


def test_the_fragment_has_no_interval_branch() -> None:
    text = shader_source("oned_sampled.frag")
    assert "#if USE_INTERVAL" not in text
    assert "IVal FI(" not in text


def test_assembled_sources_are_complete_and_the_split_kept_its_size() -> None:
    for expression in ("sin(x)", "x/sqrt(x**2)", "floor(x)"):
        _, fragment = shader_sources(sp.sympify(expression))
        assert "@" not in fragment, expression
    for expression in ("sin(x)", "tan(x)", "exp(tan(x))"):
        assert "@" not in path_compute_source(expression), expression
    # The fragment is GGB's sampled fallback only (the interval renderer is compute);
    # before the split the same expressions baked 40-52 KB fragments.
    for expression in ("sin(x)", "floor(x)", "x/sqrt(x**2)"):
        fragment = shader_sources(sp.sympify(expression))[1]
        assert len(fragment) < 30_000
