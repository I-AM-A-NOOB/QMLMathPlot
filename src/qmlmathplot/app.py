"""MVP 入口：把组件包成一个最小可验证的窗口（供 `main.py` / `uv run qmlmathplot` 调用）。

    python main.py --backend d3d11 "sin(1/x)"

RHI 后端由启动参数决定；不指定就用 Qt 的默认后端。指定时必须在
``QGuiApplication`` 之前写成 ``QSG_RHI_BACKEND`` —— Qt 在平台初始化时读它，
之后再调 ``QQuickWindow.setGraphicsApi()`` 不生效（窗口的 surface 已按默认后端
建好，实测报 "QRhiGles2: Failed to make context current"）。
"""

from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from .viewmodel import PlotController, qml_component_path, register_qml_types

# QSG_RHI_BACKEND 的合法取值（Qt 6）；不指定 = Qt 默认（Windows 上是 d3d11）
BACKENDS = ("d3d11", "d3d12", "vulkan", "metal", "opengl", "null")

DEFAULT_EXPRESSION = "sin(x)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qmlmathplot",
        description="QMLMathPlot 组件的最小验证窗口（拖拽平移、滚轮缩放）。",
    )
    parser.add_argument("expression", nargs="?", default=DEFAULT_EXPRESSION,
                        help=f"sympy 语法表达式（默认 {DEFAULT_EXPRESSION}）")
    parser.add_argument("--backend", choices=BACKENDS, default=None,
                        help="Qt Quick 的 RHI 后端；不指定则用 Qt 默认值")
    parser.add_argument("--width", type=int, default=900, help="窗口宽（默认 900）")
    parser.add_argument("--height", type=int, default=600, help="窗口高（默认 600）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if args.backend is not None:
        # 显式指定的后端优先于环境变量：命令行就是要覆盖一切
        os.environ["QSG_RHI_BACKEND"] = args.backend

    app = QGuiApplication(sys.argv[:1])
    register_qml_types()

    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setTitle(f"QMLMathPlot — {args.expression}")
    view.resize(args.width, args.height)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    if view.status() is not QQuickView.Status.Ready:
        for err in view.errors():
            print(err.toString(), file=sys.stderr)
        return 1

    root = view.rootObject()
    if root is None:
        print("QML 根对象创建失败", file=sys.stderr)
        return 1

    # MVVM：App 侧持有 ViewModel 并注入组件（组件未注入时也会自带一个）
    controller = PlotController(args.expression)
    root.setProperty("controller", controller)

    view.show()
    print(f"RHI 后端: {view.graphicsApi()}  (QSG_RHI_BACKEND={os.environ.get('QSG_RHI_BACKEND', '未设置')})",
          flush=True)
    return app.exec()
