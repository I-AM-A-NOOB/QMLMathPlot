# Rendering modes

`Plot.renderMode` selects how curves are rasterised:

| value | path |
|---|---|
| `"gpu"` (default) | `CurveRhiItem` (`QQuickRhiItem`) inside Qt Quick's RHI scene graph |
| `"cpu"` | `PathCurveItem` (`QQuickPaintedItem`) per curve, drawing with QPainter |

The QML component mirrors the model (`PlotView.renderMode: plot.renderMode`). A host can set
it in Python, pass `render_mode=` to `MathPlotWidget`, or use `qmlmathplot --render-mode` /
`--software`.

There is **no runtime `"auto"` probe**. The backend is a deployment decision: Qt reads
`QSG_RHI_BACKEND` / `QT_QUICK_BACKEND` during platform initialisation, before
`QGuiApplication`, and a live window cannot switch scene-graph backends.

## GPU mode

Everything is drawn through the single `CurveRhiItem` in one pass:

| `Program.kind` | geometry |
|---|---|
| `intervals` | compute shader (`shaders/interval_path.comp`): one sequential invocation walks the screen columns and emits the GeoGebra interval path into a storage buffer; `path.vert/path.frag` draws it |
| `implicit` | fragment shader (`shaders/implicit.frag`): per-pixel signed distance with domain/pole guards and a marching cell |
| `adaptive` | `curveplotter` produces a world-space path on the CPU; `stroke_vertices` expands it to screen quads; the shared path pipeline strokes it |
| `data` | point polyline through the same stroke pipeline |

The compute and CPU-path producers share the stroke pipeline (vertex layout, width,
round joins, MSAA), so their visual weight is the same even though the geometry source
differs.

Costs (measured on the development machine, 900×600, including `grabToImage` readback):
`sin(1/x)` ≈ 13–14 ms, `tan(x)` ≈ 12 ms per grab; the first frame of a new compute
expression additionally creates the QRhi pipeline (≈0.3–1.2 s, a QRhi limitation — the
shader is already baked before that).

## CPU mode

CPU mode is a **first-class platform target** for machines without a usable RHI backend,
not only an error fallback. Start Qt Quick with its software scene graph and select the
mode before the window is shown:

```python
import os
os.environ["QT_QUICK_BACKEND"] = "software"   # before QGuiApplication

from qmlmathplot import Plot
from qmlmathplot.calc import SymPyProgramSource

plot = Plot(program_source=SymPyProgramSource())
plot.renderMode = "cpu"
```

or:

```bash
qmlmathplot --software "sin(1/x)"
```

In CPU mode the RHI item is put behind an inactive `Loader`; it is **not instantiated**, so
a software-backend window does not log `No QRhi found for window … QQuickRhiItem will not be
functional`. Furniture (grid, axes, ticks, title, legend, annotations, guides, input) is QML
and does not depend on the curve renderer.

### Coverage

| content | CPU mode |
|---|---|
| 1-D `intervals` / `adaptive` | drawn: `curveplotter` samples `Program.math` into one `QPainterPath` per curve |
| `implicit` | **not drawn**: the view shows “implicit curves need the GPU renderer” |
| `data` (`add_points`) | **not drawn yet** (silent blank); tracked for a later release |
| programs with only `numpy` (no `math`) | **not drawn yet** (silent blank); the CPU sampler compiles `math` |
| grid / axes / ticks / title / legend / annotations / guides | drawn (QML, independent of the renderer) |
| pan / zoom | re-samples and repaints on the GUI thread |

Performance: one camera change costs about 1–7 ms per curve for typical expressions
(`curveplotter` + QPainter), more for deep/oscillatory views. Multi-curve panning on a
software backend is the main CPU-mode bottleneck.

### Export in CPU mode

`Plot.to_image()` / `savefig(backend="qt")` render an offscreen `QQuickWidget`; the widget
inherits `plot.renderMode`, so CPU mode exports through QPainter. This was verified on a
normal (RHI) desktop: `MathPlotWidget(..., render_mode="cpu")` followed by `to_image()`
produces the curve. A fully software `QQuickWidget` environment is host-dependent (Qt
Widgets still needs a platform plugin).

## Runtime failure behaviour

- A parse/analysis error is a `Program.error`: `curve.error` is set and the previous
  drawing stays.
- A failed shader bake keeps the previous shaders and sets `curve.error`.
- Pipeline/resource creation failures in the renderer currently propagate (there is no
  automatic GPU→CPU switch); use `renderMode="cpu"` up front on a platform where the RHI is
  unreliable.
- CLI/API errors do not throw into QML: the component never calls `add_curve` by itself.

## Known limitations

- The implicit (level-set) renderer is GPU-only in 0.7; CPU mode says so explicitly.
- CPU mode does not draw data curves or numpy-only programs yet.
- The compute pipeline creation remains on the render thread; the `.qsb` bake does not.
- The CPU path samples the function per camera change; there is no cross-camera geometry
  cache yet.
- A window without a usable RHI must start with the software scene graph; switching
  backends in a live window is not possible.
