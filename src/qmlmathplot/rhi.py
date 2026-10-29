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
    QRhiGraphicsPipeline,
    QRhiShaderResourceBinding,
    QRhiShaderStage,
    QRhiVertexInputLayout,
    QRhiViewport,
    QVector4D,
)
from PySide6.QtQuick import QQuickRhiItem, QQuickRhiItemRenderer

from .qsb import bake

__all__ = ["CurveRhiItem"]

_UNIFORM_SIZE = 128

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
        self._buffers: dict[str, tuple[QRhiBuffer, object]] = {}
        self._curves: list[tuple[QUrl, float, QColor]] = []
        self._camera = QVector4D(0.0, 0.0, 1.0, 1.0)
        self._size = (1.0, 1.0)
        self._update = None

    def initialize(self, cb) -> None:  # noqa: ARG002 - Qt API
        rhi = self.rhi()
        self._vertex = _qshader(QUrl.fromLocalFile(str(bake(_VERTEX_SOURCE, "vert"))))
        self._update = rhi.nextResourceUpdateBatch()

    def _buffer(self, key: str):
        pair = self._buffers.get(key)
        if pair is None:
            rhi = self.rhi()
            buf = rhi.newBuffer(QRhiBuffer.Dynamic, QRhiBuffer.UniformBuffer, _UNIFORM_SIZE)
            if not buf.create():
                raise RuntimeError("cannot create the curve uniform buffer")
            srb = rhi.newShaderResourceBindings()
            srb.setBindings([QRhiShaderResourceBinding.uniformBuffer(
                0,
                QRhiShaderResourceBinding.VertexStage | QRhiShaderResourceBinding.FragmentStage,
                buf,
            )])
            if not srb.create():
                raise RuntimeError("cannot create the curve shader resource bindings")
            pair = (buf, srb)
            self._buffers[key] = pair
        return pair

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
        pipe.setSampleCount(1)
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
        col = spec[2]
        struct.pack_into("<4f", data, 112, col.redF(), col.greenF(), col.blueF(), col.alphaF())
        return bytes(data)

    def render(self, cb) -> None:
        draws = []
        for row, spec in enumerate(self._curves):
            buf, srb = self._buffer(str(row))
            pipe = self._pipeline(spec, srb)
            if pipe is None:
                continue
            self._update.updateDynamicBuffer(buf, 0, self._uniforms(spec))
            draws.append((pipe, srb))
        cb.beginPass(self.renderTarget(), QColor(0, 0, 0, 0),
                     QRhiDepthStencilClearValue(1.0, 0), self._update)
        self._update = self.rhi().nextResourceUpdateBatch()
        if draws:
            size = self.colorTexture().pixelSize()
            cb.setViewport(QRhiViewport(0, 0, size.width(), size.height()))
            for pipe, srb in draws:
                cb.setGraphicsPipeline(pipe)
                cb.setShaderResources(srb)
                cb.draw(3)
        cb.endPass()


class CurveRhiItem(QQuickRhiItem):
    """The whole plot drawn by one QRhi pass."""

    curvesChanged = Signal()
    cameraChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._curves_model = None
        self._snapshot: list[tuple[QUrl, float, QColor]] = []
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
                    self._snapshot.append((QUrl(url), float(curve.lineWidth), QColor(curve.color)))
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
