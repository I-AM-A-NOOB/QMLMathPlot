"""滚轮缩放行为探针：往返可逆性（漂移）、锚点稳定性、速率随 delta 变化。

用法: .venv/Scripts/python.exe -u _probe_zoom.py
"""

import os
import sys

import sympy as sp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _probe_frames2 import load_module  # noqa: F401  (shared loader for the patched variants)


def make_widget():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    w = load_module("nan").SympyGLPlotter(sp.sin(sp.Symbol("x")))
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(900, 600)
    w.show()
    app.processEvents()
    return app, w


def wheel(w, x, y, dy=0, pixel_dy=0):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    pos = QPointF(x, y)
    w.wheelEvent(QWheelEvent(pos, pos, QPoint(0, pixel_dy), QPoint(0, dy),
                             Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                             Qt.ScrollPhase.NoScrollPhase, False))


def rect(w):
    return (w.xMin, w.xMax, w.yMin, w.yMax)


def rel(a, b):
    return max(abs(p - q) / max(abs(q), 1e-300) for p, q in zip(a, b))


def reset(w):
    w.xMin, w.xMax, w.yMin, w.yMax = -6.0, 6.0, -2.0, 2.0


def main():
    app, w = make_widget()
    print(f"widget {w.width()}x{w.height()}  view0={rect(w)}")
    assert w.width() > 0 and w.height() > 0

    x, y = 300.0, 200.0

    # 0) 漂移分解：光标在视图正中(u=v=0.5) 与 偏离中心
    for (px, py), tag in (((450.0, 300.0), "光标居中 u=v=0.5"), ((90.0, 200.0), "光标偏左上")):
        reset(w)
        v0 = rect(w)
        wheel(w, px, py, -120)
        wheel(w, px, py, +120)
        span0 = v0[1] - v0[0]
        print(f"[往返/{tag}] 跨度比 = {(w.xMax - w.xMin) / span0:.12f}  "
              f"中心位移 = {((w.xMin + w.xMax) / 2 - (v0[0] + v0[1]) / 2) / span0:+.6f}·span  "
              f"相对漂移 = {rel(rect(w), v0):.3e}")

    # 1) 同一位置：下滚一档再上滚一档 —— 应精确回到原视图
    v0 = rect(w)
    wheel(w, x, y, -120)
    v1 = rect(w)
    wheel(w, x, y, +120)
    print(f"\n[可逆性] 下滚一档 {tuple(round(v, 12) for v in v1)}")
    print(f"         上滚一档 {tuple(round(v, 12) for v in rect(w))}")
    print(f"         相对漂移(单次往返) = {rel(rect(w), v0):.3e}")

    # 2) 200 次往返的累计漂移
    reset(w)
    v0 = rect(w)
    for _ in range(200):
        wheel(w, x, y, -120)
        wheel(w, x, y, +120)
    print(f"[可逆性] 200 次往返后相对漂移 = {rel(rect(w), v0):.3e}")

    # 3) 锚点：缩放前后光标下的世界点应不动
    reset(w)
    ax = w.xMin + (w.xMax - w.xMin) * (x / w.width())
    ay = w.yMax - (w.yMax - w.yMin) * (y / w.height())
    wheel(w, x, y, +120)
    bx = w.xMin + (w.xMax - w.xMin) * (x / w.width())
    by = w.yMax - (w.yMax - w.yMin) * (y / w.height())
    print(f"\n[锚点] 前 ({ax:.15g}, {ay:.15g}) 后 ({bx:.15g}, {by:.15g}) "
          f"相对误差 = {max(abs(bx - ax) / abs(ax), abs(by - ay) / abs(ay)):.3e}")

    # 4) 速率应随 delta 大小变化：span_new / span_old == step**(dy/120)
    print("\n[速率] dy      span 比例      期望(step=0.9)")
    for dy in (15, 60, 120, 240):
        reset(w)
        before = w.xMax - w.xMin
        wheel(w, x, y, dy)
        print(f"       {dy:<8} {(w.xMax - w.xMin) / before:.6f}     {0.9 ** (dy / 120):.6f}")

    # 5) 只有 pixelDelta（高精度滚轮/触摸板）
    reset(w)
    before = w.xMax - w.xMin
    wheel(w, x, y, 0, pixel_dy=60)
    print(f"\n[高精度] 仅 pixelDelta=60 -> span 比例 = {(w.xMax - w.xMin) / before:.6f}"
          f"（1.0 表示被忽略）")

    # 6) 深度缩放后必须还能滚回来（旧实现会卡在极小跨度再也回不来）
    reset(w)
    v0 = rect(w)
    for _ in range(100):
        wheel(w, x, y, +120)
    s_deep = w.xMax - w.xMin
    for _ in range(100):
        wheel(w, x, y, -120)
    print(f"\n[深度往返] 100 档放大后 span={s_deep:.6g} -> 100 档缩小后 span={w.xMax - w.xMin:.6g}  "
          f"相对漂移 = {rel(rect(w), v0):.3e}")

    # 7) 极限缩放应被钳制，且视图保持有限可继续操作
    reset(w)
    for _ in range(600):
        wheel(w, x, y, +120)
    s_in = w.xMax - w.xMin
    for _ in range(1200):
        wheel(w, x, y, -120)
    s_out = w.xMax - w.xMin
    print(f"[极限] 600 档上滚后 span={s_in:.3e} → 1200 档下滚后 span={s_out:.3e}  "
          f"有限={all(v == v and abs(v) != float('inf') for v in rect(w))}")

    w.hide()


def app_section():
    """端到端：真实窗口 + 真实 WM_MOUSEWHEEL 消息。"""
    import ctypes

    from PySide6 import QtCore
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from _probe_app import build_app_module

    mod = build_app_module("sp.sin(x)")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = mod.MainWindow()
    plot = win.centralWidget()
    win.resize(900, 600)
    win.show()
    app.processEvents()

    user32 = ctypes.windll.user32
    hwnd = int(win.winId())
    WM_MOUSEWHEEL = 0x020A
    dpr = plot.devicePixelRatioF()
    g = plot.mapToGlobal(QPoint(300, 200))
    px, py = int(g.x() * dpr), int(g.y() * dpr)  # WM_MOUSEWHEEL 用物理屏幕坐标

    def wheel(z_delta):
        user32.PostMessageW(hwnd, WM_MOUSEWHEEL, (z_delta & 0xFFFF) << 16,
                            ((py & 0xFFFF) << 16) | (px & 0xFFFF))
        for _ in range(3):  # Qt 的滚轮事件排队投递，多走几轮事件循环
            app.processEvents()

    print(f"\n[端到端/真实滚轮消息] dpr={dpr:g} 物理坐标=({px},{py})")
    plot.xMin, plot.xMax, plot.yMin, plot.yMax = -6.0, 6.0, -2.0, 2.0
    v0 = rect(plot)
    wheel(120)
    v1 = rect(plot)
    wheel(-120)
    print(f"  初始 {tuple(round(t, 9) for t in v0)}")
    print(f"  zDelta=+120 -> {tuple(round(t, 9) for t in v1)}")
    print(f"  zDelta=-120 -> {tuple(round(t, 9) for t in rect(plot))}  相对漂移 = {rel(rect(plot), v0):.3e}")

    for z in (30, 240):
        plot.xMin, plot.xMax, plot.yMin, plot.yMax = -6.0, 6.0, -2.0, 2.0
        wheel(z)
        print(f"  zDelta={z:+4d}: 跨度比 = {(plot.xMax - plot.xMin) / 12.0:.6f}"
              f"（期望 {0.9 ** (z / 120):.6f}）")

    img = plot.grabFramebuffer()
    bg = (0x14, 0x14, 0x1E)
    drawn = sum(1 for y in range(0, img.height(), 6) for x in range(0, img.width(), 6)
                if abs(img.pixelColor(x, y).red() - bg[0])
                + abs(img.pixelColor(x, y).green() - bg[1])
                + abs(img.pixelColor(x, y).blue() - bg[2]) > 20)
    print(f"  缩放后曲线仍在绘制: 命中采样点 = {drawn}")
    win.hide()


if __name__ == "__main__":
    main()
    app_section()
