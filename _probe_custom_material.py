"""绕开 ShaderEffect：用 Python 自定义材质（QSGMaterialShader + 预烘 .qsb）
在 D3D11/GL 上做逐像素隐函数渲染。

动机（实测）：
  * ShaderEffect 走 Qt 的"着色器重写"路径，在 Intel D3D11 上会挂死（6/6）；
  * 库存材质（QSGVertexColorMaterial）在 D3D11 上正常（3/3）。
  => 自定义材质（自己烘 .qsb、自己填 uniform block）既没有重写、也不依赖 qsb 之外
     的东西，可能是"隐函数 + 全后端"的出路。本脚本就是验证这一点。

用法: python _probe_custom_material.py [expr]   （默认 sin(1/x)）
输出: _cm_<backend>.png，并打印点亮像素数
"""

import ctypes
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import sympy as sp
from PySide6.QtCore import QSize, QUrl
from PySide6.QtGui import QGuiApplication, QColor
from PySide6.QtQuick import (QQuickItem, QQuickView, QSGGeometry, QSGGeometryNode,
                             QSGNode, QSGMaterial, QSGMaterialShader)
from PySide6.QtQml import qmlRegisterType
from PySide6.QtTest import QTest

from qmlmathplot import core, qsb

# std140 布局（与着色器里的 block 一一对应）
OFF_MATRIX = 0      # mat4  (64 B)
OFF_OPACITY = 64    # float
OFF_VIEW = 80       # vec4
OFF_SIZE = 96       # vec2
OFF_LINE = 104      # float
OFF_COLOR = 112     # vec4
BLOCK_BYTES = 128

# QSGGeometry 只持有 AttributeSet 指针：必须保住这份拷贝（否则顶点格式悬空，
# 表现为"静默不画"或渲染线程崩溃）
_ATTRS = QSGGeometry.defaultAttributes_Point2D()


class ImplicitMaterial(QSGMaterial):
    """把 view/size/lineWidth/color 传给着色器的材质。"""

    def __init__(self) -> None:
        super().__init__()
        self.view = (0.0, 0.0, 0.0, 0.0)
        self.size = (0.0, 0.0)
        self.line_width = 1.6
        self.color = QColor("#33ccff")

    def createShader(self) -> QSGMaterialShader:
        return ImplicitShader()

    def type(self) -> int:
        return 1024

    def compare(self, other) -> int:
        if not isinstance(other, ImplicitMaterial):
            return 1
        same = (self.view == other.view and self.size == other.size
                and self.line_width == other.line_width
                and self.color == other.color)
        return 0 if same else 1


class ImplicitShader(QSGMaterialShader):
    def __init__(self) -> None:
        super().__init__()
        self.setShaderFileName(QSGMaterialShader.Stage.VertexStage, VERT_QSB)
        self.setShaderFileName(QSGMaterialShader.Stage.FragmentStage, FRAG_QSB)
        self._buf = ctypes.c_char * BLOCK_BYTES
        self._f = ctypes.c_float * 16
        self._mat = ctypes.c_float * 16
        self._view = ctypes.c_float * 4
        self._size = ctypes.c_float * 2
        self._color = ctypes.c_float * 4

    def updateUniformData(self, state, new_material, old_material) -> bool:
        raw = int(state.uniformData())
        if not raw:
            return False
        changed = False
        if state.isMatrixDirty():
            m = state.combinedMatrix()
            ctypes.memmove(raw + OFF_MATRIX, m.data(), 64)
            changed = True
        if state.isOpacityDirty():
            ctypes.c_float.from_address(raw + OFF_OPACITY).value = state.opacity()
            changed = True
        if changed or new_material is not old_material or True:
            ctypes.c_float.from_address(raw + OFF_VIEW + 0).value = new_material.view[0]
            ctypes.c_float.from_address(raw + OFF_VIEW + 4).value = new_material.view[1]
            ctypes.c_float.from_address(raw + OFF_VIEW + 8).value = new_material.view[2]
            ctypes.c_float.from_address(raw + OFF_VIEW + 12).value = new_material.view[3]
            ctypes.c_float.from_address(raw + OFF_SIZE + 0).value = new_material.size[0]
            ctypes.c_float.from_address(raw + OFF_SIZE + 4).value = new_material.size[1]
            ctypes.c_float.from_address(raw + OFF_LINE).value = new_material.line_width
            c = new_material.color
            ctypes.c_float.from_address(raw + OFF_COLOR + 0).value = c.redF()
            ctypes.c_float.from_address(raw + OFF_COLOR + 4).value = c.greenF()
            ctypes.c_float.from_address(raw + OFF_COLOR + 8).value = c.blueF()
            ctypes.c_float.from_address(raw + OFF_COLOR + 12).value = c.alphaF()
            return True
        return False


class ImplicitItem(QQuickItem):
    """整块 item 就是一个 quad，曲线完全由片元着色器算出来。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.view = (-6.0, 6.0, -2.0, 2.0)

    def updatePaintNode(self, node, _data):
        n = QSGGeometryNode()
        geo = QSGGeometry(_ATTRS, 4)
        geo.setDrawingMode(QSGGeometry.DrawingMode.DrawTriangleStrip)
        w, h = self.width(), self.height()
        buf = (ctypes.c_float * 8).from_address(int(geo.vertexData()))
        for i, (x, y) in enumerate(((0, 0), (w, 0), (0, h), (w, h))):
            buf[2 * i] = x
            buf[2 * i + 1] = y
        n.setGeometry(geo)
        mat = ImplicitMaterial()
        mat.view = self.view
        mat.size = (w, h)
        n.setMaterial(mat)
        n.setFlags(QSGNode.Flag.OwnsGeometry | QSGNode.Flag.OwnsMaterial)
        return n


QML = """
import QtQuick
import CM 1.0
Item {
    width: 900
    height: 600
    Rectangle { anchors.fill: parent; color: "#14141e" }
    ImplicitItem { anchors.fill: parent }
}
"""


def main() -> int:
    expr_src = sys.argv[1] if len(sys.argv) > 1 else "sin(1/x)"
    expr = sp.sympify(expr_src, locals={"x": sp.Symbol("x")})
    vert, frag = core.qml_shader_sources(expr)
    global VERT_QSB, FRAG_QSB
    VERT_QSB = str(qsb.bake(vert, "vert"))
    FRAG_QSB = str(qsb.bake(frag, "frag"))

    app = QGuiApplication(sys.argv[:1])
    qmlRegisterType(ImplicitItem, "CM", 1, 0, "ImplicitItem")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cm.qml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(QML)
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(900, 600)
    view.setSource(QUrl.fromLocalFile(path))
    print("status:", view.status(), flush=True)
    view.show()
    QTest.qWaitForWindowExposed(view)
    for _ in range(40):
        app.processEvents()
    img = view.grabWindow()
    name = f"_cm_{str(view.graphicsApi()).split('.')[-1]}.png"
    img.save(name)
    bg = (0x14, 0x14, 0x1E)
    lit = sum(1 for y in range(img.height()) for x in range(img.width())
              if abs(img.pixelColor(x, y).red() - bg[0])
              + abs(img.pixelColor(x, y).green() - bg[1])
              + abs(img.pixelColor(x, y).blue() - bg[2]) > 20)
    print(f"后端={view.graphicsApi()} 点亮像素={lit} -> {name}", flush=True)
    view.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
