"""Qt 官方例子 1:1 移植（guaranteed-correct 写法），用于排除我自己的用法差异。

  - QQuickItem.updatePaintNode 里 allocate()
  - 用 QSGGeometry::Point2D 直接写
  - 材质 QSGVertexColorMaterial（官方例子是这个）

用法: python _probe_docs.py
"""

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import (QQuickItem, QQuickView, QSGGeometry,
                             QSGGeometryNode, QSGNode, QSGVertexColorMaterial)
from PySide6.QtQml import qmlRegisterType
from PySide6.QtTest import QTest

P4 = ctypes.c_float * 4
C4 = (ctypes.c_ubyte * 4)
CP = ctypes.c_float * 2


class Item(QQuickItem):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.n = 6000

    def updatePaintNode(self, node, _data):
        print("updatePaintNode, node =", node, " item尺寸 =", self.width(), "x", self.height(), flush=True)
        node = QSGGeometryNode()
        geo = QSGGeometry(QSGGeometry.defaultAttributes_ColoredPoint2D(), self.n)
        geo.setDrawingMode(QSGGeometry.DrawingMode.DrawTriangleStrip)
        geo.allocate(self.n)
        node.setGeometry(geo)
        node.setMaterial(QSGVertexColorMaterial())
        node.setFlags(QSGNode.Flag.OwnsGeometry | QSGNode.Flag.OwnsMaterial)
        # Point2D 的内存布局：x(4) y(4) r g b a（ColoredPoint2D 是 12 字节/顶点）
        CP = ctypes.c_float * 2
        CA = ctypes.c_ubyte * 4
        class ColoredPoint(ctypes.Structure):
            _fields_ = [("xy", CP), ("rgba", CA)]
        buf = (ColoredPoint * self.n).from_address(int(geo.vertexData()))
        for i in range(self.n):
            t = i / self.n
            buf[i].xy[0] = t * 480
            buf[i].xy[1] = 120 + 120 * (1 if i % 2 else -1)
            buf[i].rgba[0] = 0x33
            buf[i].rgba[1] = 0xCC
            buf[i].rgba[2] = 0xFF
            buf[i].rgba[3] = 0xFF
        return node


QML = """
import QtQuick
import X 1.0
Item {
    width: 600; height: 400
    Item { id: it; x: 60; y: 60; width: 480; height: 280 }
}
"""


def main() -> int:
    app = QGuiApplication(sys.argv[:1])
    qmlRegisterType(Item, "X", 1, 0, "Item")
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(600, 400)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "min.qml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(QML)
    view.setSource(QUrl.fromLocalFile(path))
    # item 引用由 findChild 拿
    view.show()
    QTest.qWaitForWindowExposed(view)
    for _ in range(30):
        app.processEvents()
    img = view.grabWindow()
    img.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_min.png"))
    print("saved _min.png", flush=True)
    view.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
