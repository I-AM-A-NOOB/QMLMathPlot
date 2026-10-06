# QMLMathPlot

An embeddable function-plotting component for Qt Quick apps. The curve is drawn
**per pixel by a fragment shader** (implicit signed-distance model): the per-frame
cost is ∝ the pixel count and independent of the function's frequency, so
oscillating functions (e.g. `sin(1/x)`) cannot drag the frame rate down through
polyline aliasing, and no CPU-side sampling is needed.

## Quick start

One line in a QtWidgets layout (least effort):

```python
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from qmlmathplot import MathPlotWidget

app = QApplication([])
window = QWidget()
box = QVBoxLayout(window)
plot = MathPlotWidget("sin(1/x)")        # drag to pan / wheel zooms anchored at the cursor
box.addWidget(plot)
window.show()
app.exec()
```

Standalone window / command line:

```
uv run qmlmathplot "sin(1/x)"                    # equivalent to python examples/minimal.py …
uv run qmlmathplot --backend d3d11 "sin(1/x)"    # force a specific backend
python examples/explorer_qtwidgets.py            # function input box + sibling widgets
```

The examples live in `examples/`: `minimal.py` is the smallest window;
`explorer_qtwidgets.py` deliberately puts the plot widget into a "grabby"
environment (input box, `QScrollArea`, `QSplitter`, overlay) to verify the widgets
do not fight each other; `explorer_qtquick.py` (plus `explorer_qtquick.qml`) is
the pure Qt Quick demo.

## Embedding into an app

**QtWidgets**: `MathPlotWidget` is an ordinary `QWidget` (internally it bridges the
QML component into the widgets world with a `QQuickWidget` and renders into its own
FBO, so it can be stacked on top of / coexist with sibling widgets). Properties and
methods: `expression` / `error` / `line_width` / `curve_color` / `background_color` /
`aspect`,
`view_bounds()` / `reset_view()` / `zoom(delta, u, v)` / `pan_pixels(dx, dy)`, the
signals `expressionChanged` / `errorChanged`, and the underlying ViewModel exposed
as `.controller`.

**Pure QML (Qt Quick)**: just inject a ViewModel (MVVM, see below).

```python
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from qmlmathplot import PlotController, qml_component_path, register_qml_types

app = QGuiApplication([])
register_qml_types()                       # registers PlotController + MathPlot as QML types

controller = PlotController("sin(1/x)")               # ViewModel (owned by the app)
view = QQuickView()
view.setSource(QUrl.fromLocalFile(qml_component_path()))
view.rootObject().setProperty("controller", controller)
view.show()
app.exec()
```

```qml
import QmlMathPlot 1.0

MathPlot {                       // component: inject a ViewModel, or let it create its own
    anchors.fill: parent
    controller: myPlotController
    lineWidth: 1.5
    aspect: 1.0          // "view" (default) or a fixed y-unit/x-unit ratio
    curveColor: "#33ccff"
    backgroundColor: "#14141e"
}
```

Component properties: `expression` (sympy syntax; changing it regenerates the GLSL
and bakes a `.qsb`), `view` (`Qt.vector4d(xmin, xmax, ymin, ymax)`), `error` (why
the expression/bake failed, empty on success), `lineWidth`, `curveColor`, `aspect`,
`backgroundColor`. `controller` is the ViewModel, exposing `zoom(delta, u, v)` /
`panPixels(dx, dy, w, h)` / `resetView()` for the input layer to call.

## Aspect ratio

By default (`aspect: "view"`) the view rectangle is used as-is, so the two axes scale with the
widget and a curve is stretched when the widget is not the shape the view was picked for.

Set `aspect` to a number to keep the ratio of the y-unit to the x-unit fixed (matplotlib's
convention: `1.0` = square units, `2.0` = the y-axis twice as tall). The view is then
**expanded around its center — never cropped** — so the plot keeps filling the widget and
nothing that was visible disappears; one axis simply shows more world. Resizing keeps the
**scale** (world units per pixel), so a window resize or a splitter drag never zooms the
curve: the visible range grows or shrinks with the widget instead. `reset_view()` restores
the default rectangle and the ratio is re-applied from there.

```python
plot = MathPlotWidget("sin(1/x)", aspect=1.0)     # square units
plot.aspect = "view"                              # back to following the widget
```

## RHI backends

`--backend {d3d11,d3d12,vulkan,metal,opengl,null}`; **if unspecified, Qt's default
backend is used**. The implementation sets `QSG_RHI_BACKEND` before
`QGuiApplication` (Qt reads it during platform initialisation, and a later
`setGraphicsApi()` has no effect). On this machine (Intel driver 32.0.101.8826)
`d3d11` and `opengl` measured pixel-for-pixel identical.

## Structure (MVVM)

| Layer | File | Responsibility |
|---|---|---|
| Model | `src/qmlmathplot/model.py` | expression → GLSL, shader source template, `ViewRect` pan/zoom math (no Qt dependency, unit-testable) |
| ViewModel | `src/qmlmathplot/viewmodel.py` | `PlotController`: expression / view / shader URL / error; `qml_component_path()` returns the component path |
| View | `src/qmlmathplot/qml/MathPlot.qml` | pure QML: `ShaderEffect` + mouse pan / wheel zoom |
| Bake | `src/qmlmathplot/qsb.py` | GLSL → `.qsb` (PySide6 ships `qsb.exe`), cached by source hash |
| Widget | `src/qmlmathplot/widget.py` | `MathPlotWidget`: QQuickWidget bridge, drops straight into QtWidgets layouts |
| Entry | `src/qmlmathplot/app.py` | command line / standalone window (the `qmlmathplot` script, `examples/minimal.py`) |
| Examples | `examples/` | `minimal.py` (minimal), `explorer_qtwidgets.py` (input box + widget coexistence), `explorer_qtquick.py` (Qt Quick) |

## Algorithm (the two pieces that make it visually "perfect")

1. **Screen-space distance stroke**: for every pixel, compute its first-order
   distance to the curve from `f` and `f'`,
   `cov = clamp(lineWidth/2 + 0.5 − d, 0, 1)`. Equal width at any slope, naturally
   antialiased, and correct by construction at poles (`tan(x)`) and jumps.
2. **Undersampled envelope band**: when a pixel's x interval holds several
   oscillations (local period < 1 pixel), the per-pixel distance is meaningless
   (it renders as moiré). In that case the column's `[min, max]` is filled as a
   solid band — for `sin(1/x)` this equals filling its true ±1 envelope.

Both criteria are **scale-independent** and are unioned: (a) ≥ 2 direction
reversals within the column (8 sample points for an ordinary pixel, topped up to 16
inside the band region); (d) the derivative prediction `|f'|·dx` far exceeds the
measured spread (hard threshold 16× and `pred ≥ 4px`). One gate: a spread < 1.5 px
is not filled (do not turn peak ripple into a block of colour). An ordinary pixel
costs 1 `f` + 1 `f'` + 8 narrow-window samples; the wide window (±8 columns, 16
points) is only evaluated inside the band region.

## Tests

```
uv run pytest                      # everything
uv run pytest -m "not gui"         # skip the render smoke tests that open a window
QSG_RHI_BACKEND=opengl uv run pytest -m gui
```

- `test_model.py`: GLSL generation (small integer powers as repeated
  multiplication, avoiding `pow(x, y)`'s undefined behaviour for x<0), shader
  injection, view math (zoom strictly reversible, no anchor drift).
- `test_shader_bake.py`: the stage of a baked `.qsb` must match its purpose, and
  the four backend targets GLSL/HLSL/MSL/SPIR-V must all be covered.
- `test_render_smoke.py`: open a window, render, read pixels; verify the thin-line
  shape, `sin(1/x)` filling its ±1 envelope, no |y|>1 artefacts outside the central
  columns, poles not connected, and nothing drawn outside the domain.
- `test_widget.py`: `MathPlotWidget` coexisting with neighbouring widgets — the plot
  appearing inside a layout, an expression change / an invalid expression keeping the
  previous graph, **the wheel over a scroll area being consumed by the plot (zoom
  instead of scrolling the parent)**, and the plot staying out of the Tab focus chain.
  `conftest.py` provides a session-scoped `QApplication` (QtWidgets needs one, and a
  process may only have one).

## Pitfalls near the `sin(1/x)` singularity (fixed — do not repeat)

All of them (macro arguments, GPU `sin`/`cos`, stroke saturation on steep columns,
per-column phase noise) are recorded in `AGENTS.md` as symptom → cause → fix with
the measurements that settled them. After the fixes (1350×900, default view, D3D11
and OpenGL pixel-for-pixel identical): the singularity column of `sin(1/x)` is lit
over a ratio of 0.50 (= half a column filling the ±1 envelope) with flat edges, and
inside the central 41 columns no solid pixel falls outside |y|>1 (7961 before).

- Macro arguments must be parenthesized.
- GPU `sin`/`cos` are unreliable for large arguments.
- Tangent-based distance saturates on steep columns.
- Per-column sampling is phase noise for oscillating functions.

The view math lives in `ViewRect`: `effective()` derives what is actually drawn (the aspect
expansion) from the stored rectangle, and the controller keeps that effective rectangle as the
new view after a pan/zoom, so interaction always works on what is on screen.

## Domain, poles and stroke (Desmos-style)

[Desmos's engineering blog](https://engineering.desmos.com/articles/press-a-key-in-the-calculator/)
spells it out: **sample** the function into points, join them into **segments**,
stroke along the segments; "break where neighbouring points suggest a jump"; refine
extrema/zeros by bisection. The same approach is used here:

- **Stroke = distance to the sampled polyline segments** (not to the tangent). The
  tangent approximation overestimates the distance on steep or strongly curved
  parts, so the stroke thins out or even fades away (`log(x)` as x→0+, the steep
  stretch of `sin(1/x)`); measuring to the segment is **equal width everywhere**.
  Sampled over ±1 column: the polyline must be wide enough that a pixel's
  perpendicular foot lands on a segment (on a sloped line it is displaced along x
  by `m·d/√(1+m²)`); with a narrower window the distance degraded to "distance to
  the nearest endpoint" and steep parts looked thin and ragged.
- **Break on jumps**: via the **analytic pole gap** (denominator sign change, world
  units) — no vertical connector is drawn at the asymptotes of `1/x`, `tan(x)`.
  (An earlier "adjacent samples differ by more than 4 viewport heights" criterion
  was pixel-/viewport-relative, so under magnification it also cut legitimate steep
  segments; it was removed.)
- **Domain** (derived by sympy, injected as `DOM`): denominator ≠ 0, `log` argument
  > 0, `sqrt` argument ≥ 0, `asin/acos` argument ∈ [−1,1]; pixels outside the domain
  are discarded outright. NaN/inf samples take part in neither the envelope nor
  segment joining.
- **Undersampled columns** (several oscillations inside one column) are replaced
  wholesale by the envelope band: a polyline is random sawtooth in such columns.
  There are two criteria, both **scale-independent**: (a) ≥ 2 direction reversals
  within the column — 8 points in the narrow window for an ordinary pixel, and
  inside the band region 8 additional interleaved points make it 16 (with 8 points,
  random sampling misses the reversals in ~11% of columns, showing up as residual
  vertical stripes; sampling 16 points globally would cost 8 extra `f` evaluations
  for every pixel, not worth paying for a few columns); (d) the distance `pred` a
  column should traverse according to the derivative far exceeds the measured
  `spread` (hard threshold 16×, and `pred ≥ 4px`) — sub-pixel oscillation sampling
  necessarily misses reversals (it may even come out monotone), and this criterion
  does not depend on sampling randomness. Pixel-unit gates are avoided here (a
  spread measured in pixels stops being meaningful once the viewport is deep);
  poles are handled by the analytic gap instead.
- **Infinite extension at unbounded domain boundaries** (`unbounded_edges()`, sympy
  one-sided limits): `log(x)` **diverges slowly** (logarithmically) as x→0+, so a
  fixed-width sampling window never reaches deep enough values — in a deep viewport
  the visible curve lies at x≈e^y (e.g. for y∈[-16,-12], x∈[1e-7,6e-6]), while the
  leftmost sample only reaches log≈-7, so the **whole segment disappears**. Fix:
  when the window crosses such a boundary, extend the polyline along the boundary x
  into a vertical ray out to ±1e12, which draws at any depth (the ray lies on the
  boundary x, so the width is constant and the columns align; the x<0 half is cut
  away by the domain, leaving exactly half a stroke visible). `1/x` and `tan(x)`
  diverge fast, so their sampled values naturally exceed any viewport and no ray is
  needed; `sqrt` (limit 0) and `sin(1/x)` (no limit) are not on the list either.

Measured: the steep stretch of `log(x)` has a constant horizontal width of 1~2
device pixels (previously a tapering gradient); the thin line of `sin(1/x)` is
uniform in width; `1/x` and `tan(x)` are not connected across their poles;
`sin(x)`/`x^2`/`exp(-x²)sin(10x)` are unaffected.

## Performance (measured)

Startup to the first frame is about **230 ms**, attributed as follows
(Windows / D3D11 / 900×600):

| Stage | Time | Notes |
|---|---|---|
| Qt graphics device init (RHI/D3D11) | **~160 ms** | exactly what the first window's `show→expose` costs; a **bare window (no ShaderEffect) is the same**, so it is not the shader's fault |
| Window creation + QML load + first frame | ~80 ms | from the second window on (device already created) this is the order of magnitude |
| `qsb` bake | 0 / ~126 ms | cached by source hash: 0.15 ms on a hit; after a source change (code edit) it must re-bake once |
| Expression analysis (sympy) | 0.6~3 ms | `unbounded_edges` only analyses **polynomial** boundaries (`tan(x)`'s `cos(x)=0` takes 94 ms via solve+limit, while tan diverges fast and sampling is naturally deep enough, so that ray is not needed) |

Per-frame GPU cost (2400×1500, extreme zoom, worst case) is **31 ms**; about 1/7 of
that at 900×600, far below the 16.7 ms of 60 Hz. The shader source is about 6.9 KB.

## Pitfalls already hit (do not repeat)

`AGENTS.md` at the repo root carries all 21 records (symptom → cause → fix, with the
measurement that settled each one). The Qt/packaging ones that shaped the code:

- qsb decides the stage from the source file suffix (see `qsb.bake()` and
  `test_shader_bake.py`).
- Read pixels with the async `QQuickItem.grabToImage()`, never
  `QQuickWindow.grabWindow()`.
- Expressions are **macros** rather than GLSL user functions: a pixel evaluates the
  same x 9 times, and macros are preprocessor expansion with no call semantics.
