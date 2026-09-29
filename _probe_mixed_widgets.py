"""一个窗口里同时放光栅 widget（QLabel）和 QOpenGLWidget（SympyGLPlotter），
验证：GL 只被那个 widget 用，同一窗口/同一进程的其它 widget 照常走光栅路径。

用法: python _probe_mixed_widgets.py     （可加 QSG_RHI_BACKEND=vulkan 试，证明与 QSG 后端无关）
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sympy as sp
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

from test_plot import SympyGLPlotter


def main() -> int:
    app = QApplication(sys.argv[:1])
    win = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)

    label = QLabel("raster: 这行字由光栅引擎画")
    label.setStyleSheet("background:#f0f0f0; color:#101010; font-size:20px; padding:8px")
    label.setFixedHeight(60)

    plot = SympyGLPlotter(sp.sin(sp.Symbol("x")))

    layout.addWidget(label)
    layout.addWidget(plot)
    win.setCentralWidget(central)
    win.resize(700, 500)
    win.move(200, 200)
    win.show()
    for _ in range(30):
        app.processEvents()
    time.sleep(0.4)
    for _ in range(20):
        app.processEvents()

    # 光栅 widget 走 QWidget 绘制路径抓自己（不经 GL）
    limg = label.grab().toImage()
    dark = sum(1 for y in range(0, limg.height(), 3) for x in range(0, limg.width(), 3)
               if limg.pixelColor(x, y).lightness() < 120)
    # GL widget 抓自己的 FBO
    pimg = plot.grabFramebuffer()
    bg = (0x14, 0x14, 0x1F)
    curve = sum(1 for y in range(0, pimg.height(), 4) for x in range(0, pimg.width(), 4)
                if abs(pimg.pixelColor(x, y).red() - bg[0])
                + abs(pimg.pixelColor(x, y).green() - bg[1])
                + abs(pimg.pixelColor(x, y).blue() - bg[2]) > 20)

    print(f"QSG_RHI_BACKEND = {os.environ.get('QSG_RHI_BACKEND', '未设置')}")
    print(f"QLabel（光栅）深色文字采样点 = {dark}   >0 即文字仍由光栅路径绘制")
    print(f"QOpenGLWidget 曲线采样点     = {curve}  >0 即它自己的 GL 上下文在画")
    print(f"本进程里有 QQuickWindow 吗   = {any('QQuick' in type(w).__name__ for w in app.topLevelWidgets())}")
    win.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
