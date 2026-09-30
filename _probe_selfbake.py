"""能自己造 .qsb 吗？—— 纯 Python（PySide6 的 QShader/QShaderCode/QShaderKey）烘一份
.qsb，丢给 QSGMaterialShader 渲染，和 qsb.exe 的产物对照。

对照点：
  * QShader 全绑定：setShader(stage, key, code) / serialized() / fromSerialized()；
  * 缺的是 QShaderDescription（inputs/outputs/uniformBlocks 反射）——PySide6 只给了
    读取端；Qt 的 GL 后端要靠它做属性名→location 绑定、UBO 绑定。
  * 所以本脚本用 GLSL 330/440 方言（显式 layout(location)/layout(std140,binding)），
    看能不能绕过对反射元数据的依赖。

用法: python _probe_selfbake.py [expr]    输出: _sb_<backend>.png + 点亮像素数
"""

import ctypes
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication, QColor, QShader, QShaderCode, QShaderKey
from PySide6.QtQuick import (QQuickItem, QQuickView, QSGGeometry, QSGGeometryNode,
                             QSGNode, QSGMaterial, QSGMaterialShader)
from PySide6.QtQml import qmlRegisterType
from PySide6.QtTest import QTest

_ATTRS = QSGGeometry.defaultAttributes_Point2D()

# 显式 location / binding：不依赖 QShaderDescription 反射
VERT_440 = """#version 440
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 view;
    vec2 size;
    float lineWidth;
    vec4 color;
};
vec2 UV;
void main() {
    UV = vec2((gl_VertexIndex == 1 || gl_VertexIndex == 3) ? 1.0 : 0.0,
              (gl_VertexIndex >= 2) ? 1.0 : 0.0);
    gl_Position = vec4(UV * 2.0 - 1.0, 0.0, 1.0);
}
"""

FRAG_440 = """#version 440
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 view;
    vec2 size;
    float lineWidth;
    vec4 color;
};
layout(location = 0) out vec4 fragColor;
void main() {
    // 只要管线通就有颜色；顺带用 color/lineWidth 证明 uniform 真的到位了
    fragColor = vec4(color.rgb * (lineWidth > 0.0 ? 1.0 : 0.0), 1.0);
}
"""


def build_qsb(stage: QShader.Stage, src: str, version: int = 440) -> bytes:
    """纯 Python 造 .qsb：setStage + QShaderKey(源, 版本) + QShaderCode。"""
    sh = QShader()
    sh.setStage(stage)
    key = QShaderKey()
    key.setSource(QShader.Source.GlslShader)
    key.setSourceVariant(QShader.Variant.StandardShader)
    key.setSourceVersion(version)
    sh.setShader(key, QShaderCode(src.encode(), b"main"))
    return bytes(sh.serialized())


class SelfMaterial(QSGMaterial):
    def __init__(self) -> None:
        super().__init__()
        self.view = (0.0, 0.0, 0.0, 0.0)
        self.size = (0.0, 0.0)
        self.line_width = 0.0
        self.color = QColor("#ff8800")

    def createShader(self) -> QSGMaterialShader:
        return SelfShader()

    def type(self) -> int:
        return 2048

    def compare(self, other) -> int:
        return 0 if isinstance(other, SelfMaterial) and other.line_width == self.line_width else 1


class SelfShader(QSGMaterialShader):
    def __init__(self) -> None:
        super().__init__()
        self.setShaderFileName(QSGMaterialShader.Stage.VertexStage, VERT_QSB)
        self.setShaderFileName(QSGMaterialShader.Stage.FragmentStage, FRAG_QSB)

    def updateUniformData(self, state, new_material, old_material) -> bool:
        raw = int(state.uniformData())
        if not raw:
            return False
        if state.isMatrixDirty():
            ctypes.memmove(raw, state.combinedMatrix().data(), 64)
        ctypes.c_float.from_address(raw + 64).value = state.opacity()
        ctypes.c_float.from_address(raw + 104).value = new_material.line_width
        c = new_material.color
        for i, v in enumerate((c.redF(), c.greenF(), c.blueF(), c.alphaF())):
            ctypes.c_float.from_address(raw + 112 + 4 * i).value = v
        return True


class SelfItem(QQuickItem):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)

    def updatePaintNode(self, node, _data):
        n = QSGGeometryNode()
        geo = QSGGeometry(_ATTRS, 4)          # 位置属性只是占位（顶点坐标在着色器里算）
        geo.setDrawingMode(QSGGeometry.DrawingMode.DrawTriangleStrip)
        buf = (ctypes.c_float * 8).from_address(int(geo.vertexData()))
        w, h = self.width(), self.height()
        for i, (x, y) in enumerate(((0, 0), (w, 0), (0, h), (w, h))):
            buf[2 * i], buf[2 * i + 1] = x, y
        n.setGeometry(geo)
        m = SelfMaterial()
        m.line_width = 2.0
        n.setMaterial(m)
        n.setFlags(QSGNode.Flag.OwnsGeometry | QSGNode.Flag.OwnsMaterial)
        return n


QML = """
import QtQuick
import SB 1.0
Item {
    width: 900; height: 600
    Rectangle { anchors.fill: parent; color: "#14141e" }
    SelfItem { anchors.fill: parent }
}
"""


def main() -> int:
    global VERT_QSB, FRAG_QSB
    data_v = build_qsb(QShader.Stage.VertexStage, VERT_440)
    data_f = build_qsb(QShader.Stage.FragmentStage, FRAG_440)
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sb_tmp")
    os.makedirs(tmp, exist_ok=True)
    VERT_QSB = os.path.join(tmp, "self-vert.qsb")
    FRAG_QSB = os.path.join(tmp, "self-frag.qsb")
    with open(VERT_QSB, "wb") as fh:
        fh.write(data_v)
    with open(FRAG_QSB, "wb") as fh:
        fh.write(data_f)
    back = QShader.fromSerialized(data_f)
    keys = back.availableShaders()
    print(f"自造 .qsb: {len(data_f)} 字节, isValid={back.isValid()}, "
          f"条目={[(k.source(), k.sourceVersion()) for k in keys]}", flush=True)
    del keys

    app = QGuiApplication(sys.argv[:1])
    qmlRegisterType(SelfItem, "SB", 1, 0, "SelfItem")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sb.qml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(QML)
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(900, 600)
    view.setSource(QUrl.fromLocalFile(path))
    view.show()
    QTest.qWaitForWindowExposed(view)
    for _ in range(40):
        app.processEvents()
    img = view.grabWindow()
    name = f"_sb_{str(view.graphicsApi()).split('.')[-1]}.png"
    img.save(name)
    bg = (0x14, 0x14, 0x1E)
    lit = sum(1 for y in range(0, img.height(), 2) for x in range(0, img.width(), 2)
              if abs(img.pixelColor(x, y).red() - bg[0])
              + abs(img.pixelColor(x, y).green() - bg[1])
              + abs(img.pixelColor(x, y).blue() - bg[2]) > 20)
    print(f"后端={view.graphicsApi()} 点亮像素(1/4 抽样)={lit} -> {name}", flush=True)
    view.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
