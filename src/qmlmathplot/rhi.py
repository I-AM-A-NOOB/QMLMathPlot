"""One QRhi pass for every curve, instead of one QML ``ShaderEffect`` per curve.

The item is a single ``QQuickRhiItem`` covering the plot. Its renderer reads the
``CurveListModel``, bakes/downloads each curve's shader, and draws the full-screen
triangles in one pass. This is the pure-Python path to ``QQuickRhiItem``; no C++ plugin
and no Python binding to qsb are involved.
"""

from __future__ import annotations

import math
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
    QRhiVertexInputAttribute,
    QRhiVertexInputBinding,
    QRhiVertexInputLayout,
    QRhiViewport,
    QVector4D,
)
from PySide6.QtQuick import QQuickRhiItem, QQuickRhiItemRenderer

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

_PATH_VERTEX = """#version 440
layout(location = 0) in vec2 pos;
layout(location = 1) in vec4 color;
layout(location = 0) out vec4 vColor;
void main() {
    gl_Position = vec4(pos, 0.0, 1.0);
    vColor = color;
}
"""

_PATH_FRAGMENT = """#version 440
layout(location = 0) in vec4 vColor;
layout(location = 0) out vec4 fragColor;
void main() { fragColor = vColor; }
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
        self._path_pipeline = None
        self._path_buffer = None
        self._path_capacity = 0
        self._path_key = None
        self._path_cache = (b"", 0)
        self._curves: list[tuple[QUrl, float, tuple[float, float, float, float], object, str]] = []
        self._camera = QVector4D(0.0, 0.0, 1.0, 1.0)
        self._size = (1.0, 1.0)

    def initialize(self, cb) -> None:  # noqa: ARG002 - Qt API
        self._vertex = _qshader(QUrl.fromLocalFile(str(bake(_VERTEX_SOURCE, "vert"))))

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

    def _ensure_path_pipeline(self) -> None:
        if self._path_pipeline is not None:
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
                QUrl.fromLocalFile(str(bake(_PATH_VERTEX, "vert"))))),
            QRhiShaderStage(QRhiShaderStage.Fragment, _qshader(
                QUrl.fromLocalFile(str(bake(_PATH_FRAGMENT, "frag"))))),
        ])
        pipe.setVertexInputLayout(layout)
        pipe.setSampleCount(max(1, self.renderTarget().sampleCount()))
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
        self._path_pipeline = pipe

    def _path_vertices(self) -> tuple[bytes, int]:
        width, height = self._size
        cx, cy, sx, sy = self._camera.x(), self._camera.y(), self._camera.z(), self._camera.w()
        data = bytearray()
        count = 0

        def vertex(px: float, py: float, rgba) -> None:
            nonlocal count
            nx = px / width * 2.0 - 1.0
            ny = 1.0 - py / height * 2.0
            r, g, b, a = rgba
            data.extend(struct.pack("<6f", nx, ny, r * a, g * a, b * a, a))
            count += 1

        for spec in self._curves:
            func = spec[3]
            if func is None:
                continue
            rgba = spec[2]
            half = spec[1] * 0.5
            points: list[tuple[float, float]] = []
            prev_y = None
            after_break = False

            def extend_to_edge() -> None:
                if len(points) >= 2:
                    px, py = points[-1]
                    _, ppy = points[-2]
                    if 0.0 <= py <= height and abs(py - ppy) > 1e-6:
                        points.append((px, height if py >= ppy else 0.0))

            def flush() -> None:
                nonlocal points, prev_y, after_break
                if len(points) < 2:
                    points = []
                    after_break = True
                    return
                if after_break and 0.0 <= points[0][1] <= height:
                    _, p0y = points[0]
                    _, p1y = points[1]
                    if abs(p0y - p1y) > 1e-6:
                        points.insert(0, (points[0][0], height if p0y >= p1y else 0.0))
                for (x0, y0), (x1, y1) in zip(points, points[1:]):
                    dx, dy = x1 - x0, y1 - y0
                    length = math.hypot(dx, dy)
                    if length < 1e-6:
                        continue
                    nx, ny = -dy / length * half, dx / length * half
                    quad = ((x0 + nx, y0 + ny), (x0 - nx, y0 - ny),
                            (x1 - nx, y1 - ny), (x1 + nx, y1 + ny))
                    for px, py in (quad[0], quad[1], quad[2],
                                   quad[0], quad[2], quad[3]):
                        vertex(px, py, rgba)
                points = []
                prev_y = None
                after_break = True

            n = max(2, int(width * 4))
            x0 = cx - 0.5 * width * sx
            x1 = cx + 0.5 * width * sx
            if spec[4] == "log(x)":
                xs = {x0 + (i / n) * (x1 - x0) for i in range(n + 1)}
                for k in range(1, 13):
                    for sgn in (-1.0, 1.0):
                        x = sgn * 10.0 ** (-k)
                        if x0 <= x <= x1:
                            xs.add(x)
                xs = sorted(xs)
            else:
                xs = [x0 + (i / n) * (x1 - x0) for i in range(n + 1)]
            for x in xs:
                try:
                    y = float(func(x))
                except (ValueError, ZeroDivisionError, OverflowError):
                    extend_to_edge()
                    flush()
                    continue
                if not math.isfinite(y):
                    extend_to_edge()
                    flush()
                    continue
                py = (cy - y) / sy + 0.5 * height
                px = (x - cx) / sx + 0.5 * width
                if abs(y) > 1e5 or (
                    prev_y is not None and prev_y * y < 0.0 and abs(y - prev_y) > 10.0
                ):
                    extend_to_edge()
                    flush()
                else:
                    points.append((px, py))
                    prev_y = y
            flush()
        return bytes(data), count

    def _ensure_path_buffer(self, size: int) -> None:
        if self._path_buffer is not None and size <= self._path_capacity:
            return
        self._path_capacity = max(1 << 16, size)
        self._path_buffer = self.rhi().newBuffer(
            QRhiBuffer.Dynamic, QRhiBuffer.VertexBuffer, self._path_capacity)
        if not self._path_buffer.create():
            raise RuntimeError("cannot create the path vertex buffer")

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
        shader_curves = [s for s in self._curves if s[3] is None]
        path_bytes, path_count = (b"", 0)
        if any(s[3] is not None for s in self._curves):
            self._ensure_path_pipeline()
            path_bytes, path_count = self._path_vertices()
        self._uniforms_for(len(shader_curves))
        draws = []
        dirty: list[tuple[int, bytes]] = []
        for row, spec in enumerate(shader_curves):
            pipe = self._pipeline(spec, self._srb)
            if pipe is None:
                continue
            offset = row * self._stride
            data = self._uniforms(spec)
            if self._last_uniforms.get(row) != data:
                dirty.append((offset, data))
                self._last_uniforms[row] = data
            draws.append((pipe, offset))
        if not draws and path_count == 0:
            cb.beginPass(self.renderTarget(), QColor(0, 0, 0, 0),
                         QRhiDepthStencilClearValue(1.0, 0))
            cb.endPass()
            return
        if path_count:
            self._ensure_path_buffer(len(path_bytes))

        used = None
        if dirty or path_count:
            used = self.rhi().nextResourceUpdateBatch()
            if used is None:
                return
            for offset, data in dirty:
                used.updateDynamicBuffer(self._ubuf, offset, data)
            if path_count:
                used.updateDynamicBuffer(self._path_buffer, 0, path_bytes)
        try:
            cb.beginPass(self.renderTarget(), QColor(0, 0, 0, 0),
                         QRhiDepthStencilClearValue(1.0, 0), used)
            size = self.colorTexture().pixelSize()
            cb.setViewport(QRhiViewport(0, 0, size.width(), size.height()))
            for pipe, offset in draws:
                cb.setGraphicsPipeline(pipe)
                cb.setShaderResources(self._srb, 1, (0, offset))
                cb.draw(3)
            if path_count:
                cb.setGraphicsPipeline(self._path_pipeline)
                cb.setShaderResources(None)
                cb.setVertexInput(0, [(self._path_buffer, 0)])
                cb.draw(path_count, 1, 0, 0)
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
                    func = None
                    if curve.expression in _PATH_EXPRESSIONS:
                        try:
                            func = sp.lambdify(sp.Symbol("x"), curve.expr, modules="math")
                        except Exception:  # noqa: BLE001 - fall back to the shader
                            func = None
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
