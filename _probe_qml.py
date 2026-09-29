"""QML 前端验证：渲染截图 / 与 QWidget 前端的视图数学对齐 / 帧率。

用法:
  .venv/Scripts/python.exe -u _probe_qml.py [expr] [--wheel N] [--fps] [--png tag]
"""

from __future__ import annotations

import ctypes
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sympy as sp
from PySide6.QtCore import QPoint, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from qmlmathplot.qml_backend import register_qml_types

WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEWHEEL = 0x0200, 0x0201, 0x0202, 0x020A
MK_LBUTTON = 0x0001
BG = (0x14, 0x14, 0x1E)


def qt_log(_t, _c, message):
    print(f"  [qt] {message[:200]}", flush=True)


def make_view(expr: str, w: int = 900, h: int = 600) -> QQuickView:
    from importlib.resources import files

    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(w, h)
    src = os.environ.get("PROBE_QML")
    path = src if src else str(files("qmlmathplot").joinpath("qml/MathPlot.qml"))
    view.setSource(QUrl.fromLocalFile(path))
    if view.status() != QQuickView.Status.Ready:
        for e in view.errors():
            print("QML 错误:", e.toString())
        raise SystemExit(1)
    if not os.environ.get("PROBE_QML"):
        view.rootObject().setProperty("expression", expr)
    view.show()
    QTest.qWaitForWindowExposed(view)
    for _ in range(10):
        QGuiApplication.processEvents()
    return view


def pump(app, n: int = 4):
    for _ in range(n):
        app.processEvents()


def view_rect(view) -> tuple:
    v = view.rootObject().property("view")
    return (v.x(), v.y(), v.z(), v.w())  # QVector4D: x=xmin, y=xmax, z=ymin, w=ymax


def is_lit(img, x: int, y: int) -> bool:
    if not (0 <= x < img.width() and 0 <= y < img.height()):
        return False
    c = img.pixelColor(x, y)
    return abs(c.red() - BG[0]) + abs(c.green() - BG[1]) + abs(c.blue() - BG[2]) > 20


def check_against_sympy(img, expr, view, samples=(0.5, 1.0, 2.0)) -> list[str]:
    """在若干列上找被点亮的行，和 sympy 算出的 y 比较（换算成设备像素）。"""
    xmin, xmax, ymin, ymax = view
    f = sp.lambdify(sp.Symbol("x"), expr, "math")
    out = []
    for xw in samples:
        expect_y = float(f(xw))
        col = int((xw - xmin) / (xmax - xmin) * img.width())
        expect_row = int((ymax - expect_y) / (ymax - ymin) * img.height())
        rows = [r for r in range(max(0, expect_row - 8), min(img.height(), expect_row + 9))
                if is_lit(img, col, r)]
        got = (sum(rows) / len(rows)) if rows else None
        out.append(f"x={xw}: 期望行 {expect_row}, 命中 {got if got is None else round(got, 1)}")
    return out


def post_wheel(app, view, z, local_xy):
    """向 QML 窗口投递真实 WM_MOUSEWHEEL（物理像素坐标）。"""
    user32 = ctypes.windll.user32
    dpr = view.devicePixelRatio()
    g = view.mapToGlobal(QPoint(*local_xy))
    px, py = int(g.x() * dpr), int(g.y() * dpr)
    user32.PostMessageW(int(view.winId()), WM_MOUSEWHEEL,
                        (z & 0xFFFF) << 16, ((py & 0xFFFF) << 16) | (px & 0xFFFF))
    for _ in range(4):
        app.processEvents()


def post_mouse(app, view, msg, local_xy, wparam=0):
    """向 QML 窗口投递真实鼠标消息（WM_MOUSEMOVE/LBUTTON* 用客户区物理像素）。"""
    user32 = ctypes.windll.user32
    dpr = view.devicePixelRatio()
    x, y = int(local_xy[0] * dpr), int(local_xy[1] * dpr)
    user32.PostMessageW(int(view.winId()), msg, wparam, ((y & 0xFFFF) << 16) | (x & 0xFFFF))
    app.processEvents()


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    expr_src = args[0] if args else "sin(x)"
    expr = sp.sympify(expr_src)
    want_fps = "--fps" in flags
    zoom_steps = next((int(f.split("=")[1]) for f in flags if f.startswith("--zoom=")), 0)
    tag = next((f.split("=")[1] for f in flags if f.startswith("--png=")), None)

    app = QGuiApplication(sys.argv[:1])
    qInstallMessageHandler(qt_log)
    register_qml_types()
    view = make_view(expr_src)
    print(f"expr={expr_src} 窗口 {view.width()}x{view.height()} dpr={view.devicePixelRatio()} "
          f"后端={view.graphicsApi()} view={tuple(round(v, 9) for v in view_rect(view))}", flush=True)

    img = view.grabWindow()
    if tag:
        out = os.path.join(HERE, f"_qmltest/qml_{tag}.png")
        img.save(out)
        print(f"  截图 -> {out}")
    lit = sum(1 for y in range(0, img.height(), 4) for x in range(0, img.width(), 4)
              if is_lit(img, x, y))
    print(f"  被点亮的采样点 = {lit}")
    for line in check_against_sympy(img, expr, view_rect(view)):
        print("  数值校验:", line)

    # ---- 与 QWidget 前端对齐：同一个逻辑位置滚一档 ----
    print("  [对齐] zDelta=+120 @局部(300,200):", end=" ")
    post_wheel(app, view, 120, (300, 200))
    print(tuple(round(v, 9) for v in view_rect(view)))
    print("  [对齐] zDelta=-120:", end=" ")
    post_wheel(app, view, -120, (300, 200))
    print(tuple(round(v, 9) for v in view_rect(view)), "（应回到初值）")

    if zoom_steps:
        for _ in range(zoom_steps):
            post_wheel(app, view, 120, (view.width() // 2, view.height() // 2))
        pump(app, 4)
        print(f"  [深缩放 {zoom_steps} 档] view="
              f"{tuple(round(v, 12) for v in view_rect(view))}")
        img2 = view.grabWindow()
        img2.save(os.path.join(HERE, f"_qmltest/qml_zoomed_{zoom_steps}.png"))
        bg2 = (0x14, 0x14, 0x1E)
        lit2 = sum(1 for y in range(0, img2.height(), 4) for x in range(0, img2.width(), 4)
                   if abs(img2.pixelColor(x, y).red() - bg2[0])
                   + abs(img2.pixelColor(x, y).green() - bg2[1])
                   + abs(img2.pixelColor(x, y).blue() - bg2[2]) > 20)
        print(f"  缩放后点亮采样点 = {lit2}")

    if want_fps:
        frames = [0]
        view.frameSwapped.connect(lambda: frames.__setitem__(0, frames[0] + 1))
        post_mouse(app, view, WM_LBUTTONDOWN, (300, 200), MK_LBUTTON)
        t0 = time.perf_counter()
        n = 1500
        for i in range(n):
            post_mouse(app, view, WM_MOUSEMOVE, (300 + 40 + i % 120, 200 + i % 60), MK_LBUTTON)
            if i % 25 == 0:
                app.processEvents()
        post_mouse(app, view, WM_LBUTTONUP, (440, 260))
        pump(app, 6)
        dt = time.perf_counter() - t0
        print(f"  拖动 {n} 次鼠标移动耗时 {dt * 1000:.0f}ms, 渲染帧数 {frames[0]} "
              f"-> {frames[0] / dt:.1f} fps  view={tuple(round(v, 5) for v in view_rect(view))}")

    view.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
