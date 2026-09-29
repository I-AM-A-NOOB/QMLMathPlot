"""Per-frame cost of the SympyGLPlotter widget (test_plot.py).

Times the GPU inside paintGL (glFinish) plus the wall time of a
update()+event-loop cycle, for several expressions and view states.

usage: .venv/Scripts/python.exe _probe_frames2.py
"""

import importlib.util
import os
import statistics
import sys
import time
import traceback

import sympy as sp

HERE = os.path.dirname(os.path.abspath(__file__))


def load_module(variant: str = "nan"):
    src = open(os.path.join(HERE, "test_plot.py"), encoding="utf-8").read()
    if variant == "offscreen":
        src = src.replace("gl_Position = vec4(0.0 / 0.0);",
                          "gl_Position = vec4(4.0, 4.0, 0.0, 1.0);")
    elif variant == "nofilter":
        src = src.replace("if (isinf(y) || isnan(y) || abs(y) > 1e8) {", "if (false) {")
    elif variant != "nan":
        raise SystemExit(f"unknown variant {variant}")
    mod = type(sys)("probe_test_plot")
    mod.__file__ = os.path.join(HERE, "test_plot.py")
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


def make_timed_class(mod, num_points=None):
    class TimedPlotter(mod.SympyGLPlotter):
        def initializeGL(self):
            if num_points is not None:
                self.num_points = num_points
            super().initializeGL()
            self.gl_ok = bool(getattr(self, "program", None)) and self.program.isLinked()

        def paintGL(self):
            t0 = time.perf_counter()
            super().paintGL()
            self.context().functions().glFinish()
            self.gpu_ms = (time.perf_counter() - t0) * 1000.0
            self.paints = getattr(self, "paints", 0) + 1

    return TimedPlotter


def phase(w, app, label, frames=30, mutate=None):
    """Render `frames` frames, optionally mutating the view between frames."""
    gpu, wall = [], []
    for _ in range(frames):
        if mutate is not None:
            mutate(w)
        w.update()
        t0 = time.perf_counter()
        app.processEvents()
        wall.append((time.perf_counter() - t0) * 1000.0)
        gpu.append(getattr(w, "gpu_ms", float("nan")))
    gpu_ok = [g for g in gpu if g == g]
    print(f"    {label:28s} n={frames:<3d} "
          f"gpu_median={statistics.median(gpu_ok) if gpu_ok else float('nan'):9.3f}ms "
          f"gpu_max={max(gpu_ok) if gpu_ok else float('nan'):9.3f}ms "
          f"wall_median={statistics.median(wall):9.3f}ms "
          f"wall_max={max(wall):9.3f}ms  gl_ok={getattr(w, 'gl_ok', '?')}")
    return gpu_ok


def run(expr, variant="nan", label="", zoom_views=()):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    mod = load_module(variant)
    app = QApplication.instance() or QApplication(sys.argv)
    cls = make_timed_class(mod)
    w = cls(expr)
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(900, 600)
    w.show()
    app.processEvents()
    print(f"  {label or variant}: {w.glsl_func}")

    try:
        gpu_ok = phase(w, app, "static (default view)", frames=30)
        phase(w, app, "pan 1px/frame", frames=40,
              mutate=lambda w: setattr(w, "xMin", w.xMin - (w.xMax - w.xMin) / w.width())
              or setattr(w, "xMax", w.xMax - (w.xMax - w.xMin) / w.width()))
        for view in zoom_views:
            xmin, xmax = view
            w.xMin, w.xMax, w.yMin, w.yMax = xmin, xmax, -2.0, 2.0
            phase(w, app, f"zoom view=[{xmin:g},{xmax:g}]", frames=20,
                  mutate=lambda w: setattr(w, "xMin", w.xMin - (w.xMax - w.xMin) * 1e-3)
                  or setattr(w, "xMax", w.xMax - (w.xMax - w.xMin) * 1e-3))
    except Exception:
        traceback.print_exc()
    finally:
        w.hide()
    return gpu_ok


if __name__ == "__main__":
    x = sp.Symbol("x")
    print("sin(x)")
    run(sp.sin(x), label="sin(x) [nan]")
    print("sin(1/x)")
    run(sp.sin(1 / x), label="sin(1/x) [nan]",
        zoom_views=((-1e-3, 1e-3), (-1e-6, 1e-6)))
    print("sin(1/x) with finite offscreen position instead of NaN")
    run(sp.sin(1 / x), variant="offscreen", label="sin(1/x) [offscreen]")
    print("sin(1/x) with the inf/nan filter disabled entirely")
    run(sp.sin(1 / x), variant="nofilter", label="sin(1/x) [nofilter]")
