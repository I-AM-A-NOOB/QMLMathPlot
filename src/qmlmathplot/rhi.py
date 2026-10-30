"""One QRhi pass for every curve, instead of one QML ``ShaderEffect`` per curve.

The item is a single ``QQuickRhiItem`` covering the plot. Its renderer reads the
``CurveListModel``, bakes/downloads each curve's shader, and draws the full-screen
triangles in one pass. This is the pure-Python path to ``QQuickRhiItem``; no C++ plugin
and no Python binding to qsb are involved.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import Property, QByteArray, QObject, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QShader,
    QRhiBuffer,
    QRhiDepthStencilClearValue,
    QRhiComputePipeline,
    QRhiGraphicsPipeline,
    QRhiShaderResourceBinding,
    QRhiShaderResourceBindings,
    QRhiShaderStage,
    QRhiVertexInputAttribute,
    QRhiVertexInputBinding,
    QRhiVertexInputLayout,
    QRhiViewport,
    QVector4D,
)
from PySide6.QtQuick import QQuickRhiItem, QQuickRhiItemRenderer

from .ggb_compute import PATH_FRAGMENT, PATH_VERTEX, path_compute_source
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
        self._path_vertex_pipeline = None
        self._path_compute_pipelines: dict[str, QRhiComputePipeline] = {}
        self._path_compute_srbs: dict[str, QRhiShaderResourceBindings] = {}
        self._path_buffers: dict[str, QRhiBuffer] = {}
        self._path_buffer_sizes: dict[str, int] = {}
        self._path_fragment = None
        self._curves: list[tuple[QUrl, float, tuple[float, float, float, float], object, str]] = []
        self._camera = QVector4D(0.0, 0.0, 1.0, 1.0)
        self._size = (1.0, 1.0)

    def initialize(self, cb) -> None:  # noqa: ARG002 - Qt API
        self._vertex = _qshader(QUrl.fromLocalFile(str(bake(_VERTEX_SOURCE, "vert"))))
        self._path_fragment = _qshader(
            QUrl.fromLocalFile(str(bake(PATH_FRAGMENT, "frag"))))

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
        self._path_compute_srbs.clear()
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

    def _ensure_path_pipeline(self) -> None:
        if self._path_vertex_pipeline is not None:
            return
        rhi = self.rhi()
        layout = QRhiVertexInputLayout()
        layout.setBindings([QRhiVertexInputBinding(24, QRhiVertexInputBinding.Classification.PerVertex)])
        layout.setAttributes([
            QRhiVertexInputAttribute(0, 0, QRhiVertexInputAttribute.Format.Float2, 0),
            QRhiVertexInputAttribute(0, 1, QRhiVertexInputAttribute.Format.Float4, 8),
        ])
        pipe = rhi.newGraphicsPipeline()
        pipe.setShaderStages([
            QRhiShaderStage(QRhiShaderStage.Vertex, _qshader(
                QUrl.fromLocalFile(str(bake(PATH_VERTEX, "vert"))))),
            QRhiShaderStage(QRhiShaderStage.Fragment, self._path_fragment),
        ])
        pipe.setVertexInputLayout(layout)
        pipe.setCullMode(QRhiGraphicsPipeline.CullMode.None_)
        empty_srb = rhi.newShaderResourceBindings()
        if not empty_srb.create():
            raise RuntimeError("cannot create the path shader resource bindings")
        pipe.setShaderResourceBindings(empty_srb)
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
            raise RuntimeError("cannot create the path pipeline")
        self._path_vertex_pipeline = pipe

    def _path_resources(self, spec):
        expression = spec[4]
        cols = int(max(1.0, self._size[0]))
        size = cols * 12 * 6 * 4
        buf = self._path_buffers.get(expression)
        if buf is None or self._path_buffer_sizes.get(expression) != size:
            buf = self.rhi().newBuffer(
                QRhiBuffer.Static,
                QRhiBuffer.StorageBuffer | QRhiBuffer.VertexBuffer,
                size)
            if not buf.create():
                raise RuntimeError("cannot create the path vertex buffer")
            self._path_buffers[expression] = buf
            self._path_buffer_sizes[expression] = size
            self._path_compute_pipelines.pop(expression, None)
            self._path_compute_srbs.pop(expression, None)
        pipe = self._path_compute_pipelines.get(expression)
        if pipe is None:
            shader = _qshader(QUrl.fromLocalFile(
                str(bake(path_compute_source(expression), "comp"))))
            pipe = self.rhi().newComputePipeline()
            pipe.setShaderStage(QRhiShaderStage(QRhiShaderStage.Compute, shader))
            srb = self.rhi().newShaderResourceBindings()
            srb.setBindings([
                QRhiShaderResourceBinding.uniformBufferWithDynamicOffset(
                    0, QRhiShaderResourceBinding.ComputeStage, self._ubuf, 128),
                QRhiShaderResourceBinding.bufferLoadStore(
                    1, QRhiShaderResourceBinding.ComputeStage, buf),
            ])
            if not srb.create():
                raise RuntimeError("cannot create the path compute bindings")
            pipe.setShaderResourceBindings(srb)
            if not pipe.create():
                raise RuntimeError("cannot create the path compute pipeline")
            self._path_compute_pipelines[expression] = pipe
            self._path_compute_srbs[expression] = srb
        return buf, pipe, self._path_compute_srbs[expression], cols

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
        if any(s[3] is not None for s in self._curves):
            self._ensure_path_pipeline()
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
                buf, compute, csrb, cols = self._path_resources(spec)
                path_draws.append((buf, compute, csrb, offset, cols))
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

        if path_draws:
            # One sequential invocation per path curve: interval per column + GGB path
            # state -> storage buffer. The uniform upload is committed with this pass.
            cb.beginComputePass(used)
            for _buf, compute, csrb, offset, _cols in path_draws:
                cb.setComputePipeline(compute)
                cb.setShaderResources(csrb, 1, (0, offset))
                cb.dispatch(1, 1, 1)
            cb.endComputePass()
            used = None

        try:
            cb.beginPass(self.renderTarget(), QColor(0, 0, 0, 0),
                         QRhiDepthStencilClearValue(1.0, 0), used)
            size = self.colorTexture().pixelSize()
            cb.setViewport(QRhiViewport(0, 0, size.width(), size.height()))
            for pipe, offset in shader_draws:
                cb.setGraphicsPipeline(pipe)
                cb.setShaderResources(self._srb, 1, (0, offset))
                cb.draw(3)
            if path_draws:
                cb.setGraphicsPipeline(self._path_vertex_pipeline)
                cb.setShaderResources(None)
                for buf, _compute, _csrb, _offset, cols in path_draws:
                    cb.setVertexInput(0, [(buf, 0)])
                    cb.draw(cols * 12, 1, 0, 0)
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
