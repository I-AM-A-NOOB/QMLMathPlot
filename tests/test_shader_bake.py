"""Regression: the baked .qsb must match its intended use (stage + coverage of every RHI backend).

This is the root cause of the D3D11 "first frame hangs": qsb decides the stage from the
**source file suffix**, so a fragment shader written as ``.glsl`` is treated as a vertex
shader -- the fragment shader comes out as a vertex shader and D3D's pixel stage receives
invalid HLSL. The GL backend does not expose the problem (ShaderEffect assigns stages by
slot), so this must be tested on its own.
"""

import pytest
import sympy as sp
from PySide6.QtGui import QShader

from qmlmathplot import model
from qmlmathplot.qsb import bake

X = sp.Symbol("x")


@pytest.mark.parametrize(("stage", "expected"), [
    ("vert", QShader.Stage.VertexStage),
    ("frag", QShader.Stage.FragmentStage),
])
def test_baked_stage_matches_use(stage, expected):
    vert_src, frag_src = model.shader_sources(sp.sin(1 / X))
    data = bake(vert_src if stage == "vert" else frag_src, stage).read_bytes()
    shader = QShader.fromSerialized(data)
    assert shader.isValid()
    assert shader.stage() == expected


def test_baked_shader_covers_every_backend():
    """The target set of qsb --qt6: GLSL(OpenGL) / HLSL(D3D11) / MSL(Metal) / SPIR-V(Vulkan)."""
    _, frag_src = model.shader_sources(sp.sin(X))
    shader = QShader.fromSerialized(bake(frag_src, "frag").read_bytes())
    sources = {key.source() for key in shader.availableShaders()}
    assert {
        QShader.Source.GlslShader,
        QShader.Source.HlslShader,
        QShader.Source.MslShader,
        QShader.Source.SpirvShader,
    } <= sources


def test_bake_rejects_unknown_stage():
    with pytest.raises(ValueError):
        bake("void main() {}", "comp")
