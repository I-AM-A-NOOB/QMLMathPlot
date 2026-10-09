# QMLMathPlot

An embeddable function-plotting component for Qt Quick apps. The curve is drawn
**per pixel by a fragment shader** (implicit signed-distance model): the per-frame
cost is ∝ the pixel count and independent of the function's frequency, so
oscillating functions (e.g. `sin(1/x)`) cannot drag the frame rate down through
polyline aliasing, and no CPU-side sampling is needed.

The state is a **camera on an infinite canvas**: a `Plot` holds a `Camera` (where you look)
and a list of `Curve`s (what is drawn), and the QML component `PlotView` binds to it. Several
panels are several `Plot`s in a Qt layout — there is no figure, no axes box and no page.

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

An un-themed plot looks like matplotlib (white background, tab10 colours, grey grid, black
axes through the origin with the tick labels riding them) — the default style sheet is applied
at construction, so `plot.theme = "default"` is the reset.

`MathPlotWidget` keeps one curve's worth of convenience (`expression`, `error`,
`line_width`, `curve_color`, `background_color`, `aspect`, `view_bounds()`,
`reset_view()`, `zoom()`, `pan_pixels()`); everything else lives on the model behind it:

```python
plot.plot.curves.add_curve("tan(x)", color="#ff8866", label="tan")   # a second curve
plot.plot.camera.aspect = 1.0          # square units ("auto" by default)
plot.plot.grid = False
plot.plot.theme = "ggplot"
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
`aspect`, `view_bounds()` / `reset_view()` / `zoom(delta, u, v)` / `pan_pixels(dx, dy)`,
the signals `expressionChanged` / `errorChanged`, and the model itself as `.plot`.

**Pure QML (Qt Quick)**: create the model in Python and inject it (MVVM, see below).

```python
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from qmlmathplot import Plot, qml_component_path, register_qml_types

app = QGuiApplication([])
register_qml_types()                       # registers Plot, Camera, Curve and PlotView

plot = Plot()                              # the model (owned by the app)
plot.add_curve("sin(1/x)")
view = QQuickView()
view.setSource(QUrl.fromLocalFile(qml_component_path()))
view.rootObject().setProperty("plot", plot)
view.show()
app.exec()
```

```qml
import QmlMathPlot 1.0

PlotView {                       // inject a Plot, or let the component create its own
    anchors.fill: parent
    plot: myPlot

    // Arbitrary QML in world coordinates: the transform is the view's own
    Rectangle {
        x: mapToScreen(0, 0).x - 4
        y: mapToScreen(0, 0).y - 4
        width: 8; height: 8; radius: 4; color: "#ffcc00"
    }
}
```

`PlotView` exposes `mapToScreen(x, y)` / `mapFromScreen(point)` plus the forwarders
`camera` and `curves`; input handling (pan by left-drag, wheel zoom anchored at the cursor)
is built in and switchable through `camera.panEnabled` / `camera.zoomEnabled`.

The model:

| Object | Members |
|---|---|
| `Plot` | `camera`, `curves`, `xlim`/`ylim`/`aspect` (forwarded), `add_curve()` / `remove_curve()`, `available_themes`, and the style properties: `background`, `grid`, `grid_color`, `grid_width`, `grid_alpha`, `grid_style`, `axis_color`, `axis_width`, `axes_position`, `color_cycle`, `line_width`, `text_color`, `tick_color`, `tick_length`, `font_size`, `tick_font_size`, `title`, `title_color`, `title_font_size`, `title_bold`, `ticks_visible`, `theme` |
| `Camera` | `centre`, `zoom` (world units per logical pixel), `aspect`, `xlim`/`ylim` (derived), `ticks_x`/`ticks_y`, `zoomStep` / `panEnabled` / `zoomEnabled`, slots `setViewport(w, h)` / `zoom_by(delta, u, v, w, h)` / `pan_pixels(dx, dy)` / `reset()` |
| `Curve` | `expression`, `color`, `lineWidth`, `visible`, `label`, `error` (read-only), the baked `vertexShader` / `fragmentShader` |
| `CurveListModel` | the curves as a model (`curve`, `expression`, `color`, `lineWidth`, `visible`, `vertexShader`, `fragmentShader` roles) so QML puts one `ShaderEffect` behind each |

Every setter is a Qt `Property` with a notify signal, so the same calls work from QML, from a
Qt Designer slot or from a `QTimer`:

```python
QTimer.singleShot(1000, lambda: setattr(curve, "expression", "sin(3/x)"))
```

## Design notes

The design — the camera model, the Qt property/signal contract, the object model and the
build order — is specified in [`docs/api-design.md`](docs/api-design.md). In short: the
Matplotlib *vocabulary* is kept where it is vocabulary (limits, grid, title, ticks, annotate),
while the *mechanism* is refused — no `Figure`/`Axes`/`subplots` (one view per widget, no
bounded page), no `FigureCanvas`/`draw()` (the scene is live), no toolbar and no legend (host
chrome), and an export is a canvas region at a pixel size rather than a re-rendered page
(§9, implemented — see *Export* below).

## The camera (centre, zoom, aspect)

`Camera` stores three numbers that do not depend on the widget: `centre` (world coordinates in
the middle of the view), `zoom` (world units per **logical** pixel on x) and `aspect` (the
ratio of the y scale to the x scale). `xlim` / `ylim` are *derived* from them and the last
reported size, so they always say what is on screen; assigning them moves the camera.

- `aspect = "auto"` (the default) keeps the two scales independent: the widget's shape decides
  how much canvas is visible, and neither axis is tied to the other. A number keeps the ratio
  fixed (matplotlib's convention: `1.0` = square units, `2.0` = the y-unit twice as tall).
  Setting it only ever **shows more** — the camera keeps its centre and expands, never crops.
- **Resizing never zooms**: the scales are kept and the visible range follows the widget, so
  dragging a window edge or a splitter shows more or less canvas instead of scaling the curve.
- `camera.reset()` returns to the home view (the classic 12 x 4 window, resolved for the
  current size); the aspect stays as configured.
- The home is a *view*, so the first size report turns it into scales for that widget: opening
  a narrow window shows the same window of canvas rather than a sliver of it.

```python
plot = MathPlotWidget("sin(1/x)", aspect=1.0)     # square units
plot.aspect = "auto"                              # back to following the widget's shape
plot.plot.camera.zoom_by(120, 0.5, 0.5, w, h)     # one wheel notch, anchored at the centre
```

## Themes

`Plot.theme` applies a style sheet to the style properties above; the default is
matplotlib's own default style, so an untouched plot already looks like matplotlib and
`plot.theme = "default"` is the reset:

```python
plot.theme = "ggplot"                # any of the bundled sheets
plot.theme = "default"               # back to matplotlib's look
plot.theme = "my-style"              # themes/my-style.mplstyle next to the package
plot.theme = "path/to/any.mplstyle"  # any file in the same format
plot.available_themes                 # the names, for a host's picker (both demos use one)
```

The 26 bundled sheets are Matplotlib's own styles, vendored verbatim into
`qmlmathplot/themes/*.mplstyle` and converted at load time: Matplotlib's `rcParams` keys are
mapped onto this library's property names, and keys describing things this library does not
have (spines, figure size, dpi, legend, marker styles, …) are ignored. `themes.names()` lists
them (`"default"` plus the 26 sheets). An unknown name raises `KeyError` and leaves the current
theme alone.

## Axes, grid and ticks

- The **grid** is drawn in QML (a `Canvas`), one line per tick position, in `grid_color` /
  `grid_width` / `grid_alpha` / `grid_style` (`-`, `--`, `:`, `-.`).
- The **axes** are the lines through world (0,0) — the x-axis at `y = 0`, the y-axis at
  `x = 0` — drawn in `axis_color` / `axis_width`, and the **tick marks and labels ride those
  axes** instead of hugging the item's edges. When 0 is off screen the axis sticks to the
  nearest edge, so its labels are never lost (and a label never leaves the item); the grid
  stays where it is. `plot.axes_position = "edge"` restores the edge-pinned behaviour.
- `camera.viewport` reports the last size the view sent, so a host can size an export from it.
- The tick *values* are nice numbers computed in Python (`Camera.tick_values()` /
  `nice_ticks()`, steps of 1/2/5 x 10^n) and pushed through `ticksChanged`; QML only positions
  and formats them, so a frame never recomputes ticks.
- `plot.ticks_visible = False` hides the whole furniture (axes, marks and labels).

## Export

`savefig()` writes a file, `to_image()` returns a `QImage`; both take the same keywords:

```python
plot.savefig("out.png", xlim=(-1, 1), ylim=(-1, 1), width=1200, height=400)
plot.savefig("hi.png", dpi=2.0)                        # the live view, twice the pixels
image = plot.to_image(width=800, height=600)           # -> QImage
plot.to_image()                                        # the live view, its own size
```

- `xlim` / `ylim` default to the **camera**: the live centre and scale, extended to the export
  size. The *region* is a parameter, so an export can cover more canvas than the widget shows.
- `width` / `height` are **device pixels** and default to the live view's device size; `dpi`
  multiplies both (`dpi=2.0` doubles the pixels).
- `adjustable` reconciles a requested range with a requested size when their aspects disagree —
  Matplotlib's parameter, and its own default:

  | `adjustable` | What happens |
  |---|---|
  | `"box"` (**default**) | one scale, taken from the live scale ratio, chosen so the requested range fits: the range is centred and the leftover pixels stay background — *nothing is distorted* |
  | `"datalim"` | the camera's own scales, with the limits expanding (never cropped) to fill the size |
  | `"stretch"` | the requested range maps onto the requested size exactly — independent scales, so a mismatched aspect **distorts** (the "report figure" mode) |

  With no explicit range all three agree, because the range derived from the camera already has
  the export size's aspect.
- `transparent=True` clears to alpha 0.
- Everything else follows the live styling (curves, grid, axes, theme), so an export cannot
  drift from what the user sees. Rendering goes through an offscreen `QQuickWidget`, so the
  export needs a `QApplication` (a clear error is raised instead of a crash if there is none).

### Vector output, matplotlib and sympy

`to_matplotlib()` hands the plot to matplotlib (one `Axes`) and `savefig(..., backend="matplotlib")`
renders through it, so `.svg`/`.pdf` work; `to_sympy()` builds the same figure through
`sympy.plotting`, which is what you want when the figure should live in sympy's world:

```python
figure = plot.to_matplotlib(xlim=(-1, 1), ylim=(-1, 1))    # -> matplotlib.figure.Figure
plot.savefig("out.svg", backend="matplotlib")              # vector, matplotlib's own savefig
handle = plot.to_sympy(xlim=(-1, 1))
handle.save("out.png")
```

matplotlib is an **optional** extra (`pip install "qmlmathplot[matplotlib]"`); nothing in the
package imports it until one of these is called, and the error names the extra. `sympy` itself is
a hard dependency, so `to_sympy()` needs matplotlib only because its plotting backend does.
Neither hand-off needs a Qt application: a `Plot` works in a plain script, which is what makes it
usable next to a notebook or a matplotlib-only tool.

**This is a second renderer, and a sampled one.** matplotlib draws polylines, so each curve is
evaluated at `samples` points (default 2000) and an oscillating function such as `sin(1/x)`
aliases — exactly what the Qt view avoids by evaluating the expression per pixel (sympy's own
adaptive sampler warns about this for the same reason, and it is available on the object
`to_sympy()` returns). Fonts and antialiasing are matplotlib's, not the Qt view's, so the two
renderers cannot look identical, and the style mapping is partial: background, grid (on/off and
colour/width/alpha/dash), per-curve colour and width, tick and text colours, font sizes, title,
axis colour/width, the limits, the aspect with matplotlib's own `adjustable`, and
`axes_position="zero"` (spines through the origin, no box). What it buys is vector output and
matplotlib's ecosystem — nothing else.

## RHI backends

`--backend {d3d11,d3d12,vulkan,metal,opengl,null}`; **if unspecified, Qt's default
backend is used**. The implementation sets `QSG_RHI_BACKEND` before
`QGuiApplication` (Qt reads it during platform initialisation, and a later
`setGraphicsApi()` has no effect). On this machine (Intel driver 32.0.101.8826)
`d3d11` and `opengl` measured pixel-for-pixel identical.

## Structure (model / view)

| Layer | File | Responsibility |
|---|---|---|
| Model | `src/qmlmathplot/model.py` | expression → GLSL, shader source template (no Qt dependency, unit-testable) |
| Camera | `src/qmlmathplot/camera.py` | `Camera`: centre / zoom / aspect, the derived ranges, pan/zoom/reset, the nice-number tick algorithm |
| Curves | `src/qmlmathplot/curve.py` | `Curve` (expression → baked `.qsb`, style, error) and `CurveListModel` |
| Plot | `src/qmlmathplot/plot.py` | `Plot`: the camera, the curves and the styling the view binds to |
| View | `src/qmlmathplot/qml/PlotView.qml` (+ `view.py`) | QML: background, grid, ticks/title, one `ShaderEffect` per curve, pan/zoom input; `view.py` registers the types and yields the component path |
| Themes | `src/qmlmathplot/themes.py` + `themes/` | the vendored Matplotlib style sheets, resolved onto the `Plot`'s properties |
| Export | `src/qmlmathplot/export.py` | `Plot.savefig()` / `to_image()`: the canvas region rendered offscreen at a pixel size (QtWidgets is imported lazily) |
| Hand-off | `src/qmlmathplot/mpl.py` | `Plot.to_matplotlib()` / `to_sympy()` / `savefig(backend="matplotlib")`: a sampled second renderer for vector output (matplotlib is an optional extra, imported lazily) |
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
  injection (including the camera uniform and the mapping), and the camera math
  (the home view at the first size, resize keeping the scale, an anchored zoom that
  is exactly reversible, the aspect expanding but never cropping, ticks).
- `test_curve.py`: a failed expression keeps the last working shaders, the style
  setters are idempotent, and the list model reports the right roles.
- `test_plot.py`: the plot's properties convert, notify exactly once and are wired
  (a PySide property with an unresolved notify name silently loses it), the defaults are
  matplotlib's default style, the limits are forwarded to the camera, and a theme applies
  (and re-applies as a reset) while an unknown name is rejected.
- `test_themes.py`: the vendored style sheets resolve to this library's properties.
- `test_shader_bake.py`: the stage of a baked `.qsb` must match its purpose, and
  the four backend targets GLSL/HLSL/MSL/SPIR-V must all be covered.
- `test_render_smoke.py`: open a window, render, read pixels; verify the thin-line
  shape, `sin(1/x)` filling its ±1 envelope, no |y|>1 artefacts outside the central
  columns, poles not connected, nothing drawn outside the domain, and the QML
  furniture (grid lines exactly at the tick positions, the axes crossing at the origin
  with their labels on them, the axis pinning to the edge once the origin is panned
  away, the title).
- `test_widget.py`: `MathPlotWidget` coexisting with neighbouring widgets — the plot
  appearing inside a layout, an expression change / an invalid expression keeping the
  previous graph, curves added/hidden/removed through the model, **the wheel over a
  scroll area being consumed by the plot (zoom instead of scrolling the parent)**, the
  fixed aspect being undistorted by the widget's shape, and the plot staying out of the
  Tab focus chain. `conftest.py` provides a session-scoped `QApplication` (QtWidgets
  needs one, and a process may only have one).
- `test_qtquick_coexistence.py`: the same drag conflict in Qt Quick (a `Flickable`
  must not steal the pan).
- `test_handoff.py`: the matplotlib/sympy hand-off — the figure reflects the limits, aspect,
  `adjustable`, grid, title, spines at zero and per-curve style; the sampling is uniform and NaN
  outside the domain (so `log(x)` breaks); `savefig(backend="matplotlib")` writes real `.svg`,
  `.pdf` and `.png`; `to_sympy().save()` writes a file and keeps per-curve colours; importing
  `qmlmathplot` does not import matplotlib, and hiding matplotlib produces an error naming the
  extra.
- `test_export.py`: `to_image()`/`savefig()` — the image is exactly the requested device size
  (`dpi` multiplies it), the no-argument form reproduces the live centre and scale, the three
  `adjustable` modes reconcile range and size as documented (a world 45-degree line stays at 45
  in `"box"`, with background margins; `"stretch"` fills the frame and distorts), the live
  camera and background survive an export, and `transparent` clears to alpha 0.

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
Every visible curve is one full-screen fragment pass, so `Curve.visible` is the lever
when a figure carries many of them.

## Pitfalls already hit (do not repeat)

`AGENTS.md` at the repo root carries all the records (symptom → cause → fix, with the
measurement that settled each one). The Qt/packaging ones that shaped the code:

- qsb decides the stage from the source file suffix (see `qsb.bake()` and
  `test_shader_bake.py`).
- Read pixels with the async `QQuickItem.grabToImage()`, never
  `QQuickWindow.grabWindow()`.
- Expressions are **macros** rather than GLSL user functions: a pixel evaluates the
  same x 9 times, and macros are preprocessor expansion with no call semantics.
- PySide ties a property's notify signal to the `Signal` **object**; a name string
  silently leaves the property without one (and QML bindings then never refresh).
- Snap **hairlines** (grid) to a half pixel, but never a **styled** line: rounding the
  axis position put the 1.07 px y-axis half off the centre column, so the line came out
  pale where it should be black.
- `QQuickWidget.setProperty(name, …)` sets it on the **widget**, not on the QML root (the
  widget has no such property, so it silently does nothing): inject the model with
  `widget.rootObject().setProperty("plot", plot)`, as `MathPlotWidget` does.

## Releasing

1. Bump `version` in `pyproject.toml` and commit.
2. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`.
3. `.github/workflows/publish.yml` then runs the non-GUI test suite, builds the sdist and wheel
   with `uv build`, and uploads them to PyPI. The tag must match `pyproject.toml` or the run
   fails before anything is published.

Authentication is PyPI's **trusted publishing** (OIDC) — no API token, no repository secret.
Configure it once at <https://pypi.org/manage/account/publishing/> (and the same on
test.pypi.org) with the owner, the repository name, the workflow file name `publish.yml` and the
environment `pypi`. A project that does not exist on PyPI yet uses the "pending publisher" form
there and is created by the first successful run. Running the workflow manually
(*Actions → Publish to PyPI → Run workflow*) publishes to TestPyPI by default, so a release can
be rehearsed:

```
pip install -i https://test.pypi.org/simple/ qmlmathplot
```

If the publish step fails with `invalid-publisher: valid token, but no corresponding
publisher`, the trusted-publisher entry is missing or does not match. The action prints the
claims it presented (`repository`, `workflow_ref`, `environment`) — the entry must match them
exactly, **including the environment name**, and it must exist on the index the run targets: a
manual run goes to **test.pypi.org**, a tag push to **pypi.org**, so both need an entry.

The workflow runs on Windows: that is the platform this library is verified on (the shader baker
`qsb` ships inside the PySide6 wheel and is only confirmed to be present there, see `qsb.py`),
and the GUI tests need a GPU scene graph no runner has. The artifact is `py3-none-any`, so the
platform does not change what is published. The GUI tests stay a developer-machine thing:
`uv run pytest` (everything) versus `uv run pytest -m "not gui"` (what CI runs).
