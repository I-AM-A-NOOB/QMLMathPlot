"""方案 B 的关键验证：QSGGeometryNode（无自定义着色器 → 无 qsb）在 D3D11 上能不能画？

顶点填充走 ctypes：QSGGeometry.vertexData() 返回 Shiboken.VoidPtr，
int() 能拿到地址，于是用 ctypes 写 float2 缓冲（内存速度）。

用法: python _probe_geom.py [N=20000] [expr=sin(x)]
"""

import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sympy as sp
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication, QColor
from PySide6.QtQuick import (QQuickItem, QQuickView, QSGFlatColorMaterial,
                             QSGGeometry, QSGGeometryNode, QSGNode,
                             QSGVertexColorMaterial)
from PySide6.QtQml import qmlRegisterType
from PySide6.QtTest import QTest

from qmlmathplot.core import ViewRect

POINT2D = ctypes.c_float * 2   # defaultAttributes_Point2D 的元素布局


class ProbeGeomItem(QQuickItem):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.view = ViewRect()
        self.expr = sp.sin(sp.Symbol("x"))
        self.count = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
        self._mat_color = None
        self.timings: list[float] = []
        self._fn = None

    @property
    def fn(self):
        if self._fn is None:
            self._fn = sp.lambdify(sp.Symbol("x"), self.expr, "math")
        return self._fn

    def updatePaintNode(self, node, _data):
        print("  updatePaintNode 被调用, node =", node, flush=True)
        # 官方 Qt 文档的写法：第一次先 allocate，再每次写 vertexData
        if node is not None and node.geometry().vertexCount() != self.count:
            print("   [更正] 重建 geometry 以匹配 count", self.count, flush=True)
            node = None
        w = float(self.width())
        h = float(self.height())
        xmin, xmax = self.view.xmin, self.view.xmax
        span = xmax - xmin
        n = self.count

        t0 = time.perf_counter()
        if node is None:
            import os as _os
            attrs = (QSGGeometry.defaultAttributes_Point2D()
                     if _os.environ.get("PROBE_POINT2D")
                     else QSGGeometry.defaultAttributes_ColoredPoint2D())
            geo = QSGGeometry(attrs, n)
            mode = (QSGGeometry.DrawingMode.DrawTriangleStrip
                    if os.environ.get("PROBE_TRIS") else QSGGeometry.DrawingMode.DrawLineStrip)
            geo.setDrawingMode(mode)
            print("   drawingMode:", mode, flush=True)
            geo.allocate(n)   # Qt6：显式分配（RHI 需要）
            if _os.environ.get("PROBE_POINT2D"):
                mat = QSGFlatColorMaterial()
                self._mat_color = QColor("#33ccff")
                mat.setColor(self._mat_color)
                print("   material color set:", self._mat_color.name(), flush=True)
            else:
                mat = QSGVertexColorMaterial()
                print("   material: 顶点色", flush=True)
            node = QSGGeometryNode()
            node.setGeometry(geo)
            node.setMaterial(mat)
            node.setFlags(QSGNode.Flag.OwnsGeometry | QSGNode.Flag.OwnsMaterial)
            # 对照实验：只画 4 个点的极简折线，排除 20000 点/大缓冲的嫌疑
            if os.environ.get("PROBE_MINI"):
                mini = QSGGeometry(QSGGeometry.defaultAttributes_Point2D(), 4)
                mini.setDrawingMode(mode)
                import os as _o
                pt = ctypes.c_float * 2
                CP = ctypes.c_ubyte * 4
                class ColoredPoint(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float), ("r", CP._type_ if False else ctypes.c_ubyte), ("g", ctypes.c_ubyte), ("b", ctypes.c_ubyte), ("a", ctypes.c_ubyte)]
                ColoredPoint4 = ColoredPoint * 4
                buf = ColoredPoint4.from_address(int(mini.vertexData())) if not _o.environ.get("PROBE_POINT2D") else (POINT2D * 4).from_address(int(mini.vertexData()))
                for idx, (px, py) in enumerate(((0,0),(600,400),(300,100),(600,0))):
                    buf[idx].x = px; buf[idx].y = py
                    buf[idx].r = 0x33; buf[idx].g = 0xCC; buf[idx].b = 0xFF; buf[idx].a = 0xFF
                mini.markVertexDataDirty()
                node.setGeometry(mini)
                print("   PROBE_MINI: 顶点换成 4 个手工点", flush=True)
        geo = node.geometry()
        print("   [call2] geometry ok", flush=True)
        try:
            vp = geo.vertexData()
            print("   [call2] vertexData ok:", int(vp), flush=True)
            buf = (POINT2D * 4).from_address(int(vp))
            buf[0][0] = 99.0
            print("   [call2] 试写 buf[0].x =", buf[0][0], flush=True)
        except Exception:
            import traceback; traceback.print_exc(); sys.stdout.flush()
        print("  顶点缓冲地址:", int(vp), flush=True)
        buf = (POINT2D * n).from_address(int(vp))
        print("   fill 开始 n=", n, flush=True)
        fn = self.fn
        scale_x = w / span
        scale_y = h / (self.view.ymax - self.view.ymin)
        # 采样 + 投屏到设备独立像素（Qt Quick 会再按 dpr 上采样）
        for i in range(n):
            x = xmin + span * (i / n)
            y = fn(x)
            buf[i][0] = (x - xmin) * scale_x
            buf[i][1] = (self.view.ymax - y) * scale_y

        print("   fill 完成后 markDirty", flush=True)
        if hasattr(geo, "vertexDataAsColorPoint") or "Colored" in str(geo.attributes()):
            cb = (ctypes.c_ubyte * (n * 8)).from_address(int(geo.vertexData()))
            for i in range(n):
                cb[i*8+2] = 0x33; cb[i*8+3] = 0xCC; cb[i*8+4] = 0xFF; cb[i*8+7] = 0xFF
        print("   前3个顶点:", [(round(buf[i][0],1), round(buf[i][1],1)) for i in range(3)], flush=True)
        ys = [buf[i][1] for i in range(0, n, 97)]
        print("   y min/max:", round(min(ys),2), "/", round(max(ys),2), flush=True)
        xs = [buf[i][0] for i in range(0, n, 97)]
        print("   x min/max:", round(min(xs),2), "/", round(max(xs),2), flush=True)
        print("   item 宽高:", self.width(), self.height(), " 视图:", self.view.as_tuple(), flush=True)
        print("   material color:", mat is None if False else self._mat_color, " node visible:", self.isVisible(), flush=True)
        geo.markVertexDataDirty()
        node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)
        return node


QML = """
import QtQuick
import GeomProbe 1.0
Item {
    width: 600
    height: 400
    Rectangle { anchors.fill: parent; color: "#14141e" }
    ProbeGeomItem { anchors.fill: parent }
}
"""


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    expr = sp.sympify(sys.argv[2]) if len(sys.argv) > 2 else sp.sin(sp.Symbol("x"))

    app = QGuiApplication(sys.argv[:1])
    qmlRegisterType(ProbeGeomItem, "GeomProbe", 1, 0, "ProbeGeomItem")  # 运行时只收 str
    doc_path = "geom_probe.qml"
    with open(doc_path, "w", encoding="utf-8") as fh:
        fh.write(QML)

    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(600, 400)
    view.setSource(QUrl.fromLocalFile(doc_path))
    view.show()
    QTest.qWaitForWindowExposed(view)
    item = view.rootObject().findChild(ProbeGeomItem)
    item.count = n
    item.expr = expr
    item.update()
    for _ in range(30):
        app.processEvents()

    img = view.grabWindow()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_geom_{n}.png")
    img.save(out)
    bg = (0x14, 0x14, 0x1E)
    lit = sum(1 for y in range(0, img.height(), 4) for x in range(0, img.width(), 4)
              if abs(img.pixelColor(x, y).red() - bg[0])
              + abs(img.pixelColor(x, y).green() - bg[1])
              + abs(img.pixelColor(x, y).blue() - bg[2]) > 20)
    times = item.timings
    med = sorted(times)[len(times) // 2] if times else float("nan")
    print(f"后端={view.graphicsApi()} N={n} 点亮采样点={lit} "
          f"updatePaintNode 填充中位={med:.2f}ms 采样次数={len(times)} -> {out}", flush=True)
    view.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
