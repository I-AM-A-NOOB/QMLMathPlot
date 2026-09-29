"""Faithful end-to-end test of the real app: real visible window, real Windows
mouse messages posted to the window (Qt's own input path), heartbeat timer to
detect GUI-thread stalls, plus a visual-change metric (does the picture actually
move between drag frames?).

usage: .venv/Scripts/python.exe -u _probe_app.py sin1x [zoomsteps]
"""

import ctypes
import os
import re
import statistics
import sys
import time

import sympy as sp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _probe_frames2 import load_module, make_timed_class  # noqa: E402

EXPR_LINE = re.compile(r"^(\s*)expr = .*$", re.M)


def build_app_module(expr_src, points=None, func_body=None):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_plot.py")
    src = open(path, encoding="utf-8").read()
    src, n = EXPR_LINE.subn(lambda m: f"{m.group(1)}expr = {expr_src}", src, count=1)
    assert n == 1, "MainWindow 里的 expr = ... 行没找到"
    if points is not None:
        src = src.replace("self.num_points = 100000", f"self.num_points = {points}")
    if func_body is not None:
        src = src.replace("return {self.glsl_func};", f"return {func_body};")
    mod = type(sys)("probe_app")
    mod.__file__ = path
    exec(compile(src, path, "exec"), mod.__dict__)
    mod.SympyGLPlotter = make_timed_class(mod)
    return mod


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "sin1x"
    zoomsteps = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    expr_src = "sp.sin(1/x)" if which == "sin1x" else "sp.sin(x)"
    size = sys.argv[3] if len(sys.argv) > 3 else "900x600"
    points = int(sys.argv[4]) if len(sys.argv) > 4 else None
    body = sys.argv[5] if len(sys.argv) > 5 else None
    sw, sh = (int(v) for v in size.split("x"))

    from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    mod = build_app_module(expr_src, points=points, func_body=body)
    app = QApplication(sys.argv[:1])
    win = mod.MainWindow()
    plot = win.centralWidget()
    win.resize(sw, sh)
    win.show()
    app.processEvents()
    time.sleep(0.4)
    app.processEvents()

    print(f"expr={expr_src} renderer={plot.context().functions().glGetString(0x1F01)} "
          f"visible={win.isVisible()} points={plot.num_points}", flush=True)

    beats = [0, 0]  # count, max gap
    stamps = [time.perf_counter()]
    timer = QTimer()
    timer.setInterval(25)

    def beat():
        now = time.perf_counter()
        beats[1] = max(beats[1], (now - stamps[0]) * 1000)
        stamps[0] = now
        beats[0] += 1

    timer.timeout.connect(beat)
    timer.start()
    app.processEvents()

    if zoomsteps:
        for _ in range(zoomsteps):
            pos = QPointF(plot.width() * 0.5, plot.height() / 2)
            plot.wheelEvent(QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, 120),
                                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                        Qt.ScrollPhase.NoScrollPhase, False))
        app.processEvents()
        print(f"  zoomed: x=[{plot.xMin:.5g},{plot.xMax:.5g}] y=[{plot.yMin:.5g},{plot.yMax:.5g}]",
              flush=True)

    # ---- real Windows mouse messages -------------------------------------
    user32 = ctypes.windll.user32
    hwnd = int(win.winId())
    WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
    MK_LBUTTON = 0x0001
    top_left = win.mapToGlobal(QPoint(0, 0))
    ox, oy = top_left.x() + 300, top_left.y() + 200  # a point inside the plot area

    def lp(x, y):
        return (y << 16) | (x & 0xFFFF)

    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp(ox, oy))
    paints0 = getattr(plot, "paints", 0)
    gpu = []
    t_start = time.perf_counter()
    moves = 1500
    for i in range(moves):
        x = ox + 40 + (i % 120)
        y = oy + (i % 60)
        user32.PostMessageW(hwnd, WM_MOUSEMOVE, MK_LBUTTON, lp(x, y))
        if i % 25 == 0:  # keep the queue realistic, not flooded
            app.processEvents()
        gpu.append(getattr(plot, "gpu_ms", float("nan")))
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp(ox + 160, oy))
    drain = time.perf_counter()
    app.processEvents()
    elapsed = time.perf_counter() - t_start
    queue_drain = time.perf_counter() - drain
    paints = getattr(plot, "paints", 0) - paints0
    gpu_ok = [g for g in gpu if g == g]
    print(f"  injected {moves} mouse moves in {elapsed * 1000:.0f}ms -> paints={paints} "
          f"fps={paints / elapsed:.1f} queue_drain={queue_drain * 1000:.0f}ms", flush=True)
    if gpu_ok:
        print(f"  gpu median={statistics.median(gpu_ok):.3f}ms max={max(gpu_ok):.3f}ms "
              f"p95={sorted(gpu_ok)[int(len(gpu_ok) * 0.95)]:.3f}ms", flush=True)
    print(f"  heartbeat={beats[0]} max_gap={beats[1]:.0f}ms  "
          f"view=[{plot.xMin:.4g},{plot.xMax:.4g}]", flush=True)

    # ---- does the picture visibly change between drag frames? ------------
    from PySide6.QtWidgets import QApplication as _QApp
    def frame():
        _QApp.processEvents()
        return plot.grabFramebuffer()

    diffs = []
    prev = frame()
    dx = (plot.xMax - plot.xMin) / plot.width() * 3
    for _ in range(20):
        plot.xMin -= dx
        plot.xMax -= dx
        cur = frame()
        changed = 0
        for yy in range(0, cur.height(), 4):
            for xx in range(0, cur.width(), 4):
                a, b = prev.pixel(xx, yy), cur.pixel(xx, yy)
                if a != b:
                    changed += 1
        diffs.append(changed / ((cur.width() // 4 + 1) * (cur.height() // 4 + 1)))
        prev = cur
    print(f"  pixels changed per 3px pan: median={statistics.median(diffs) * 100:.2f}% "
          f"min={min(diffs) * 100:.2f}%", flush=True)
    timer.stop()
    win.hide()


if __name__ == "__main__":
    main()
