"""在**默认 D3D11 后端**（不设任何环境变量）上，测试"精简版 uniform block"
的 ShaderEffect（只有 view/size，像 run_baked.py 那个 4/4 成功的版本）
能否渲染 —— 用来判断 D3D11 挂死是不是被我们的 shader 内容触发的。

用法: python _probe_d3d_trim.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sympy as sp
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from qmlmathplot import core, qsb
from qmlmathplot.qml_backend import register_qml_types

# 把 core 的片元着色器改回"只有 view/size"的形态（去掉 lineWidth/color 与它们的引用）
_orig = core.qml_shader_sources


def trimmed(expr):
    vert, frag = _orig(expr)
    frag = (frag.replace("    float lineWidth;  // 线宽（逻辑像素）\n", "")
                .replace("    vec4 color;       // 非预乘 rgba\n", "")
                .replace("color.rgb * a", "vec3(0.2, 0.8, 1.0) * a")
                .replace("color.a * qt_Opacity", "qt_Opacity")
                .replace("lineWidth * 0.5", "0.5"))
    return vert, frag


core.qml_shader_sources = trimmed

QML = """
import QtQuick
import QmlMathPlot 1.0
Item {
    width: 600
    height: 400
    Rectangle { anchors.fill: parent; color: "#14141e" }
    PlotController { id: backend; expression: "sin(x)" }
    ShaderEffect {
        anchors.fill: parent
        property vector4d view: Qt.vector4d(-6, 6, -2, 2)
        property vector2d size: Qt.vector2d(width, height)
        vertexShader: backend.vertexShader
        fragmentShader: backend.fragmentShader
    }
}
"""


def main() -> int:
    env_note = os.environ.get("QSG_RHI_BACKEND", "未设置")
    app = QGuiApplication(sys.argv[:1])
    register_qml_types()
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "d3d_trim.qml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(QML)
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(600, 400)
    view.setSource(QUrl.fromLocalFile(path))
    view.show()
    QTest.qWaitForWindowExposed(view)
    for _ in range(30):
        app.processEvents()
    img = view.grabWindow()
    bg = (0x14, 0x14, 0x1E)
    lit = sum(1 for y in range(0, img.height(), 4) for x in range(0, img.width(), 4)
              if abs(img.pixelColor(x, y).red() - bg[0])
              + abs(img.pixelColor(x, y).green() - bg[1])
              + abs(img.pixelColor(x, y).blue() - bg[2]) > 20)
    print(f"后端={view.graphicsApi()} 点亮采样点={lit}", flush=True)
    view.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
