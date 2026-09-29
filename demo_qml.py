"""QML 前端演示：python demo_qml.py [表达式]

左键拖动平移；滚轮以光标为锚点缩放（上下滚严格可逆）。
曲线由片元着色器逐像素绘制，着色器首次用到时用 PySide6 自带的 qsb 烘成 .qsb。

    python demo_qml.py "sin(1/x)"
    python demo_qml.py "exp(-x*x)*sin(10*x)"
"""

from __future__ import annotations

import sys
from importlib.resources import files

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from qmlmathplot.qml_backend import prefer_opengl_backend, register_qml_types

DEFAULT_EXPRESSION = "sin(x)"


def qml_path() -> str:
    return str(files("qmlmathplot").joinpath("qml/MathPlot.qml"))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    expression = argv[1] if len(argv) > 1 else DEFAULT_EXPRESSION

    # D3D11 后端在本机画 ShaderEffect 会偶发挂死，demo 默认走 OpenGL。
    # 必须在 QGuiApplication 之前调用（它靠环境变量生效）；已有 QSG_RHI_BACKEND 时不覆盖。
    prefer_opengl_backend()
    app = QGuiApplication(argv[:1])
    register_qml_types()

    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setTitle(f"QMLMathPlot — {expression}")
    view.resize(900, 600)
    view.setSource(QUrl.fromLocalFile(qml_path()))
    if view.status() != QQuickView.Status.Ready:
        for err in view.errors():
            print(err.toString(), file=sys.stderr)
        return 1

    root = view.rootObject()
    if root is None:
        print("QML 根对象创建失败", file=sys.stderr)
        return 1
    root.setProperty("expression", expression)
    view.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
