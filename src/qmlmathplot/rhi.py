"""One QRhi pass for every curve, instead of one QML ``ShaderEffect`` per curve.

The item is a single ``QQuickRhiItem`` covering the plot. Its renderer reads the
``CurveListModel``, bakes/downloads each curve's shader, and draws the full-screen
triangles in one pass. This is the pure-Python path to ``QQuickRhiItem``; no C++ plugin
and no Python binding to qsb are involved.
"""

from __future__ import annotations

import struct
from pathlib import Path

import sympy as sp

from PySide6.QtCore import Property, QByteArray, QObject, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QShader,
    QRhiBuffer,
    QRhiDepthStencilClearValue,
    QRhiGraphicsPipeline,
    QRhiShaderResourceBinding,
    QRhiShaderStage,
    QRhiVertexInputLayout,
    QRhiViewport,
    QVector4D,
)
from PySide6.QtQuick import QQuickRhiItem, QQuickRhiItemRenderer

from .model import _pole_glsl, domain_glsl, func_glsl, has_sign_pole
from .qsb import bake

__all__ = ["CurveRhiItem"]

_UNIFORM_SIZE = 128
_PATH_EXPRESSIONS = {"tan(x)", "log(x)"}

_VERTEX_SOURCE = """#version 440
layout(location = 0) out vec2 vUV;
void main() {
    vec2 p = vec2(float((gl_VertexIndex << 1) & 2), float(gl_VertexIndex & 2));
    vUV = vec2(p.x, 1.0 - p.y);
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

_PATH_FRAGMENT = """#version 440
layout(location = 0) in vec4 vColor;
layout(location = 0) out vec4 fragColor;
void main() { fragColor = vColor; }
"""


def _path_vertex_source(expression: str) -> str:
    expr = sp.sympify(expression)
    func = func_glsl(expr, "u", wrap=True)
    domain = domain_glsl(expr, "u") or "true"
    pole = _pole_glsl(expr, "u") if has_sign_pole(expr) else "1.0"
    has_log = "1" if expression == "log(x)" else "0"
    return f"""#version 440
layout(location = 0) out vec4 vColor;
layout(std140, binding = 0) uniform buf {{
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 camera;
    vec2 size;
    float lineWidth;
    vec4 color;
}};
#define TAU 6.283185307179586
float _wrap(float t) {{ return t - TAU * floor(t * (1.0 / TAU)); }}
#define SIN(t) sin(_wrap(t))
#define COS(t) cos(_wrap(t))
#define F(u) ({func})
#define DOM(u) ({domain})
#define POLE(u) ({pole})
#define HAS_LOG {has_log}
void main() {{
    int n = int(max(1.0, size.x) * 4.0);
    int idx = gl_InstanceIndex;
    float xmin = camera.x - 0.5 * size.x * camera.z;
    float step = camera.z / 4.0;
    float x0;
    float x1;
    if (HAS_LOG != 0 && idx >= n) {{
        int k = idx - n + 1;
        float p = step * pow(10.0, -float(k + 1));
        x0 = p;
        x1 = p * 10.0;
    }} else {{
        x0 = xmin + float(idx) * step;
        x1 = x0 + step;
    }}
    float y0 = 0.0;
    float y1 = 0.0;
    bool valid = DOM(x0) && DOM(x1) && POLE(x0) * POLE(x1) > 0.0;
    if (valid) {{
        y0 = F(x0);
        y1 = F(x1);
        valid = y0 == y0 && y1 == y1 && abs(y0) < 1e30 && abs(y1) < 1e30;
    }}
    vec2 p0 = vec2((x0 - camera.x) / camera.z + 0.5 * size.x,
                   (camera.y - y0) / camera.w + 0.5 * size.y);
    vec2 p1 = vec2((x1 - camera.x) / camera.z + 0.5 * size.x,
                   (camera.y - y1) / camera.w + 0.5 * size.y);
    vec2 d = p1 - p0;
    float len = length(d);
    vec2 nrm = len > 1e-6 ? vec2(-d.y, d.x) / len * lineWidth * 0.5 : vec2(0.0);
    vec2 p;
    if (gl_VertexIndex == 0) p = p0 + nrm;
    else if (gl_VertexIndex == 1) p = p0 - nrm;
    else if (gl_VertexIndex == 2) p = p1 - nrm;
    else if (gl_VertexIndex == 3) p = p0 + nrm;
    else if (gl_VertexIndex == 4) p = p1 - nrm;
    else p = p1 + nrm;
    if (!valid) {{
        p = vec2(10.0);
    }}
    gl_Position = vec4(p.x / size.x * 2.0 - 1.0,
                       1.0 - p.y / size.y * 2.0, 0.0, 1.0);
    vColor = vec4(color.rgb * color.a, color.a);
}}
"""


def _qshader(path: QUrl) -> QShader:
    return QShader.fromSerialized(QByteArray(Path(path.toLocalFile()).read_bytes()))


class _Renderer(QQuickRhiItemRenderer):
    def __init__(self, item: "CurveRhiItem") -> None:
        super().__init__()
        self._vertex = None
        self._pipelines: dict[tuple[str, ...], QRhiGraphicsPipeline] = {}
        self._ubuf: QRhiBuffer | None = None
        self._srb = None
        self._stride = 0
        self._capacity = 0
        self._last_uniforms: dict[int, bytes] = {}
        self._path_pipelines: dict[str, QRhiGraphicsPipeline] = {}
        self._path_fragment = None
        self._curves: list[tuple[QUrl, float, tuple[float, float, float, float], object, str]] = []
        self._camera = QVector4D(0.0, 0.0, 1.0, 1.0)
        self._size = (1.0, 1.0)

    def initialize(self, cb) -> None:  # noqa: ARG002 - Qt API
        self._vertex = _qshader(QUrl.fromLocalFile(str(bake(_VERTEX_SOURCE, "vert"))))
        self._path_fragment = _qshader(
            QUrl.fromLocalFile(str(bake(_PATH_FRAGMENT, "frag"))))

    def _uniforms_for(self, count: int) -> None:
        """One uniform buffer, one binding object, one dynamic offset per curve."""
        if count <= self._capacity and self._srb is not None:
            return
        rhi = self.rhi()
        self._stride = max(_UNIFORM_SIZE, rhi.ubufAlignment())
        self._capacity = max(8, count)
        self._ubuf = rhi.newBuffer(QRhiBuffer.Dynamic, QRhiBuffer.UniformBuffer,
                                   self._stride * self._capacity)
        if not self._ubuf.create():
            raise RuntimeError("cannot create the curve uniform buffer")
        self._last_uniforms.clear()
        self._srb = rhi.newShaderResourceBindings()
        self._srb.setBindings([QRhiShaderResourceBinding.uniformBufferWithDynamicOffset(
            0,
            QRhiShaderResourceBinding.VertexStage | QRhiShaderResourceBinding.FragmentStage,
            self._ubuf,
            _UNIFORM_SIZE,
        )])
        if not self._srb.create():
            raise RuntimeError("cannot create the curve shader resource bindings")

    def _pipeline(self, spec, srb) -> QRhiGraphicsPipeline | None:
        fragment = spec[0]
        key = (fragment.toString(),)
        pipe = self._pipelines.get(key)
        if pipe is not None:
            return pipe
        if len(self._pipelines) >= 32:
            # A live expression edit creates a new .qsb; keep the cache bounded across edits.
            self._pipelines.clear()
        try:
            fs = _qshader(fragment)
        except (OSError, ValueError):
            return None
        if not fs.isValid():
            return None
        pipe = self.rhi().newGraphicsPipeline()
        pipe.setShaderStages([
            QRhiShaderStage(QRhiShaderStage.Vertex, self._vertex),
            QRhiShaderStage(QRhiShaderStage.Fragment, fs),
        ])
        pipe.setVertexInputLayout(QRhiVertexInputLayout())
        pipe.setShaderResourceBindings(srb)
        pipe.setRenderPassDescriptor(self.renderTarget().renderPassDescriptor())
        pipe.setSampleCount(max(1, self.renderTarget().sampleCount()))
        blend = QRhiGraphicsPipeline.TargetBlend()
        blend.enable = True
        blend.srcColor = QRhiGraphicsPipeline.BlendFactor.One
        blend.dstColor = QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha
        blend.srcAlpha = QRhiGraphicsPipeline.BlendFactor.One
        blend.dstAlpha = QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha
        blend.opColor = QRhiGraphicsPipeline.BlendOp.Add
        blend.opAlpha = QRhiGraphicsPipeline.BlendOp.Add
        pipe.setTargetBlends([blend])
        if not pipe.create():
            return None
        self._pipelines[key] = pipe
        return pipe

    def _path_pipeline(self, spec, srb):
        expression = spec[4]
        key = expression
        pipe = self._path_pipelines.get(key)
        if pipe is not None:
            return pipe
        try:
            vertex = bake(_path_vertex_source(expression), "vert")
            vs = _qshader(QUrl.fromLocalFile(str(vertex)))
        except (OSError, ValueError):
            return None
        if not vs.isValid():
            return None
        pipe = self.rhi().newGraphicsPipeline()
        pipe.setShaderStages([
            QRhiShaderStage(QRhiShaderStage.Vertex, vs),
            QRhiShaderStage(QRhiShaderStage.Fragment, self._path_fragment),
        ])
        pipe.setVertexInputLayout(QRhiVertexInputLayout())
        pipe.setShaderResourceBindings(srb)
        pipe.setRenderPassDescriptor(self.renderTarget().renderPassDescriptor())
        pipe.setSampleCount(max(1, self.renderTarget().sampleCount()))
        blend = QRhiGraphicsPipeline.TargetBlend()
        blend.enable = True
        blend.srcColor = QRhiGraphicsPipeline.BlendFactor.One
        blend.dstColor = QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha
        blend.srcAlpha = QRhiGraphicsPipeline.BlendFactor.One
        blend.dstAlpha = QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha
        blend.opColor = QRhiGraphicsPipeline.BlendOp.Add
        blend.opAlpha = QRhiGraphicsPipeline.BlendOp.Add
        pipe.setTargetBlends([blend])
        if not pipe.create():
            return None
        self._path_pipelines[key] = pipe
        return pipe

    def synchronize(self, item: "CurveRhiItem") -> None:
        self._camera = QVector4D(item.camera)
        self._size = (max(1.0, item.width()), max(1.0, item.height()))
        self._curves = list(item._snapshot)

    def _uniforms(self, spec) -> bytes:
        data = bytearray(_UNIFORM_SIZE)
        struct.pack_into("<16f", data, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
        struct.pack_into("<f", data, 64, 1.0)
        c = self._camera
        struct.pack_into("<4f", data, 80, c.x(), c.y(), c.z(), c.w())
        struct.pack_into("<2f", data, 96, *self._size)
        struct.pack_into("<f", data, 104, spec[1])
        struct.pack_into("<4f", data, 112, *spec[2])
        return bytes(data)

    def render(self, cb) -> None:
        if not self._curves:
            cb.beginPass(self.renderTarget(), QColor(0, 0, 0, 0),
                         QRhiDepthStencilClearValue(1.0, 0))
            cb.endPass()
            return
        self._uniforms_for(len(self._curves))
        shader_draws = []
        path_draws = []
        dirty: list[tuple[int, bytes]] = []
        for row, spec in enumerate(self._curves):
            offset = row * self._stride
            data = self._uniforms(spec)
            if self._last_uniforms.get(row) != data:
                dirty.append((offset, data))
                self._last_uniforms[row] = data
            if spec[3] is None:
                pipe = self._pipeline(spec, self._srb)
                if pipe is not None:
                    shader_draws.append((pipe, offset))
            else:
                pipe = self._path_pipeline(spec, self._srb)
                if pipe is not None:
                    instances = int(max(1.0, self._size[0]) * 4.0)
                    if spec[4] == "log(x)":
                        instances += 12
                    path_draws.append((pipe, offset, instances))
        if not shader_draws and not path_draws:
            cb.beginPass(self.renderTarget(), QColor(0, 0, 0, 0),
                         QRhiDepthStencilClearValue(1.0, 0))
            cb.endPass()
            return

        used = None
        if dirty:
            used = self.rhi().nextResourceUpdateBatch()
            if used is None:
                return
            for offset, data in dirty:
                used.updateDynamicBuffer(self._ubuf, offset, data)
        try:
            cb.beginPass(self.renderTarget(), QColor(0, 0, 0, 0),
                         QRhiDepthStencilClearValue(1.0, 0), used)
            size = self.colorTexture().pixelSize()
            cb.setViewport(QRhiViewport(0, 0, size.width(), size.height()))
            for pipe, offset in shader_draws:
                cb.setGraphicsPipeline(pipe)
                cb.setShaderResources(self._srb, 1, (0, offset))
                cb.draw(3)
            for pipe, offset, instances in path_draws:
                cb.setGraphicsPipeline(pipe)
                cb.setShaderResources(self._srb, 1, (0, offset))
                cb.draw(6, instances, 0, 0)
            cb.endPass()
        finally:
            if used is not None:
                # PySide's beginPass does not hand the batch back to QRhi; without this
                # release the pool exhausts after 64 frames.
                used.release()



class CurveRhiItem(QQuickRhiItem):
    """The whole plot drawn by one QRhi pass."""

    curvesChanged = Signal()
    cameraChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._curves_model = None
        self._snapshot: list[tuple[QUrl, float, tuple[float, float, float, float], object, str]] = []
        self._camera = QVector4D(0.0, 0.0, 1.0, 1.0)
        self._renderer = None
        self.setAlphaBlending(True)

    def createRenderer(self):
        self._renderer = _Renderer(self)
        return self._renderer

    @Property(QObject, notify=curvesChanged)
    def curves(self):
        return self._curves_model

    @curves.setter
    def curves(self, value) -> None:
        if self._curves_model is not None:
            try:
                self._curves_model.rowsInserted.disconnect(self._curves_changed)
                self._curves_model.rowsRemoved.disconnect(self._curves_changed)
                self._curves_model.dataChanged.disconnect(self._curves_changed)
            except (RuntimeError, TypeError):
                pass
        self._curves_model = value
        if value is not None:
            value.rowsInserted.connect(self._curves_changed)
            value.rowsRemoved.connect(self._curves_changed)
            value.dataChanged.connect(self._curves_changed)
        self._refresh()
        self.curvesChanged.emit()

    def _refresh(self) -> None:
        """Snapshot the model on the GUI thread; the renderer must not touch it."""
        self._snapshot = []
        model = self._curves_model
        if model is not None:
            for row in range(model.rowCount()):
                curve = model.at(row)
                if curve is None or not curve.visible:
                    continue
                url = curve.fragmentShader
                if url.isLocalFile():
                    col = curve.color
                    func = curve.expression if curve.expression in _PATH_EXPRESSIONS else None
                    self._snapshot.append((
                        QUrl(url),
                        float(curve.lineWidth),
                        (col.redF(), col.greenF(), col.blueF(), col.alphaF()),
                        func,
                        curve.expression,
                    ))
        self.update()

    def _curves_changed(self, *args) -> None:  # noqa: ARG002 - Qt signal payload
        self._refresh()

    @Property(QVector4D, notify=cameraChanged)
    def camera(self) -> QVector4D:
        return self._camera

    @camera.setter
    def camera(self, value: QVector4D) -> None:
        self._camera = QVector4D(value)
        self.cameraChanged.emit()
        self.update()
