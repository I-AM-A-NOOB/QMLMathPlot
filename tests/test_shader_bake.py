"""回归：烘出来的 .qsb 必须与用途匹配（stage + 覆盖所有 RHI 后端）。

这是 D3D11 "第一帧挂死" 的根因：qsb 靠**源文件后缀**判 stage，写成 ``.glsl``
会被当成 vertex —— 于是片元着色器烘出来是顶点着色器，D3D 的像素阶段拿到非法
HLSL（``D3DCompile ps_5_0`` 报 ``X4502 invalid output semantic 'TEXCOORD0'``），
Intel 驱动则表现为挂死。GL 后端不会暴露这个问题（ShaderEffect 按槽位赋 stage），
所以这条必须单独测。
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
    """qsb --qt6 的目标集：GLSL(OpenGL) / HLSL(D3D11) / MSL(Metal) / SPIR-V(Vulkan)。"""
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
