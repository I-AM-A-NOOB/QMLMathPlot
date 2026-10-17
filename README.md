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

`add_curve()` (and `Curve.expression`) accept the expression **either as a string or as a sympy
expression**: `plot.add_curve(sp.sin(1 / sp.Symbol("x")))` is the same curve as
`plot.add_curve("sin(1/x)")`. The stored and reported form is always the canonical string — QML,
a project file and the text field want one — while the parsed expression is cached and exposed as
`Curve.expr`, which is what re-baking and the matplotlib hand-off use. The object form exists so a
curve can be composed without string interpolation:

```python
curve = plot.add_curve("sin(x)")
plot.add_curve(sp.diff(sp.sympify(curve.expression, locals={"x": sp.Symbol("x")})), label="f'")
```

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
| `Plot` | `camera`, `curves`, `xlim`/`ylim`/`aspect` (forwarded), `add_curve()` / `add_implicit()` / `remove_curve()`, `available_themes`, and the style properties: `background`, `grid`, `grid_color`, `grid_width`, `grid_alpha`, `grid_style`, `axis_color`, `axis_width`, `axes_position`, `color_cycle`, `line_width`, `text_color`, `tick_color`, `tick_length`, `font_size`, `tick_font_size`, `title`, `title_color`, `title_font_size`, `title_bold`, `ticks_visible`, `theme` |
| `Camera` | `centre`, `zoom` (world units per logical pixel), `aspect`, `xlim`/`ylim` (derived), `ticks_x`/`ticks_y`, `zoomStep` / `panEnabled` / `zoomEnabled`, slots `setViewport(w, h)` / `zoom_by(delta, u, v, w, h)` / `pan_pixels(dx, dy)` / `reset()` |
| `Curve` | `expression` (string or sympy expression), `expr` (the parsed expression, read-only), `implicit`, `color`, `lineWidth`, `visible`, `label`, `error` (read-only), the baked `vertexShader` / `fragmentShader` |
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

## Implicit curves

An expression whose free symbols are `x` **and** `y` is drawn as the level set `F(x, y) = 0`.
The artist follows the expression, whoever sets it: `add_curve` routes it by itself, assigning to
`curve.expression` re-routes the same curve (so a text field can turn `sin(x)` into a circle),
and `add_implicit` asks for the level-set artist explicitly — also for a function of `x` alone,
which is a pair of vertical lines. Only a *third* free symbol is refused: substitute parameters
first.

```python
plot.add_curve("x**2 + y**2 - 1")        # a circle: {x, y} routes to the level-set artist
plot.add_implicit("x*y - 1")             # the same artist, asked for by name
plot.curves.at(0).expression = "x**2 + y**2 - 4"   # re-routes the curve that is already there
plot.add_curve("a*sin(x)")               # ValueError: substitute `a` first
```

The renderer is the same idea as the 1-D stroke, one dimension up: one evaluation of `F` and its
two partials per pixel give a *candidate* distance to the level set in pixels,
`d = |F| / sqrt((Fx·dx)² + (Fy·dy)²)`, and the coverage is
`clamp(lineWidth/2 + 0.5 - d, 0, 1)` — analytic antialiasing and a stroke width that does not
depend on the angle, with no sampling grid (the unit circle measures the same 3 device pixels at
0°, 45°, 90° and 135° for `lineWidth=2` at DPR 1.5).

That quotient is a pseudo-zero at a pole and at a diverging domain boundary, so it is
**certified by projecting onto the level set**: two damped Newton steps from the pixel (or, for
a pixel outside the domain, from the nearest in-domain sample), accepting only a converged
residual (`|F| <= 0.25·R·|grad F|`) and using the distance to the projected point. The domain
therefore clips the *curve*, not the stroke: the half of the band lying outside is painted, so
`x-log(y-1)` does not thin out as it approaches `y = 1` and `y-log(x)` reaches `x = 0` in a deep
view. A `POLE(x,y)` factor (e.g. `cos(y)` for `tan(y)`) rejects a normal probe that crosses a
singularity, so `x-tan(y)` does not paint its asymptotes; far-out garbage lines are still
refused (the stroke is allowed one radius outside the domain, no more).

Known limit: **smooth, single-sheet level sets**. Thin features and several branches through one
pixel (`sin(1/(x*x + y*y))`) are not handled yet — the assessment is `docs/api-design.md` §6b.
The matplotlib hand-off skips implicit curves (a level set has no polyline to sample), while the
Qt export renders them like anything else.

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
aliases — exactly what the Qt view avoids by evaluating the expression per pixel. SymPy does not
solve it either, and its adaptive mode does not help: measured on `sin(1/x)` over [-1, 1],
`sympy.plotting.plot(adaptive=True)` uses **289** points (fewer than a fixed 2000), emits no
warning in sympy 1.14, and draws the same block of vertical connectors. The difference is how
the two fail: a sampled renderer draws an artefact, the Qt view draws the envelope the data
actually supports. (`to_sympy()` returns the object sympy made, so its own `adaptive` mode is
available there.) Fonts and antialiasing are matplotlib's, not the Qt view's, so the two
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

The layers are strict about Qt. **`model.py` (expression → GLSL), `themes.py` (style-sheet
conversion) and `mpl.py` (the sampled hand-off) import no Qt at all** — they are unit-testable
without a `QApplication`. `Camera`, `Curve` and `Plot` are the **ViewModel**: `QObject`s whose
`Property`es carry notify signals, which is precisely what the QML component binds to — and why
a Python-side assignment updates the view with no call in between. `view.py`, `PlotView.qml`,
`export.py` and `widget.py` are the View/embedding layer and do need Qt; `export.py` and
`widget.py` import QtWidgets lazily, so a pure-QML host never loads it.

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

## Algorithm (the march + the interval column)

For every pixel the shader **walks the curve** instead of sampling a fixed x grid, and
that is what removes the whole class of "sampling wins/loses against the geometry":

1. **Seed** on the curve at this pixel's column (a pole or domain-edge column probes just
   off itself), then **project the pixel onto the tangent there** — one dot product and one
   evaluation — so the walk starts at the pixel's own foot. On a steep curve the column's
   curve point can be a whole viewport away (in arc length), and no budget can walk that
   far, which used to dash the line.
2. **March** along the curve in both directions, one *logical pixel of arc length* per
   step (the stride follows the estimated screen slope; `f'` bootstraps it). Stroke by
   **distance to the joined polyline segments** (Desmos' approach): equal width at any
   slope, naturally antialiased — the width is a property of the line, not of the slope
   (measured on `k·x`: 3.00, 3.00, 3.00, 2.94 device px at slopes 2/10/40/200 against a
   nominal 3.0, where the old ±1-column window gave 2.24/2.10/2.05/2.02).
3. **Join invariant**: a segment only ever joins two samples within **2.5 px** of each
   other. Chords of 1-px-arc steps are ~1.2 px on any continuous curve, so a jump across
   a pole — *or across any discontinuity the static analysis never recognised* — is
   hundreds of px: the connector is not detected and cut, it cannot be formed
   (`x/sqrt(x**2)` = sign: measured a 454-px column at x=0 through the old pole guards).
4. **Escape rule**: three *receding* steps outside the pixel's stroke-radius ball end a
   direction — the curve is monotonically away. A reversal resets the count (it may well
   come back). This bounds a steep monotone branch (`log`'s descent) without any
   viewport-relative gap.
5. **Interval column band** — GeoGebra's column idea, on the GPU. The whole expression is
   also evaluated in interval arithmetic over the pixel's column (generated from sympy in
   `interval.py`, compiled to the GLSL `IVal FI(xlo, xhi)`): a conservative y-enclosure with
   topology **connected / two rays around a gap / whole / empty**. The band is that
   enclosure plus a half-stroke feather. There is no ±8-column window, no violation-count
   gate and no chord-stroke for supported expressions:
   - `sin(1/x)`'s transition columns get the exact lobe (measured overfill: 1.66 world
     units before, 0.0099 after);
   - `tan`'s pole column is a gap, an extreme zoom-out is `whole` (the screen fills, as in
     GeoGebra);
   - `log`'s boundary is `[-∞, log h]` — the boundary ray is gone for supported
     expressions;
   - `x**(1/3)` uses the real odd root (and the scalar `F` prints `_real_pow`).
   A wide finite enclosure is checked against the column's own samples for the interval
   **dependency** artifact (a dependent quotient such as `x/sqrt(x**2)` is left to the
   march). The band itself is always the pixel's own column.

6. **Column polyline, stroked — GeoGebra's sampling on the GPU.** GeoGebra samples one
   interval per column and strokes the polyline through the samples with the vector line
   width. A pixel's own column is at its own x (distance 0), so the shader evaluates the
   **neighbouring** columns (`x ± dx`) too. For a finite monotone column it joins their
   interval ranges with **diagonal chords** (previous→current, current→next) and strokes
   the chords; that is the smooth stroke on a steep `tan`/`log` branch (the old vertical
   column rectangles produced 1-column staircases, visible as comb edges). For a **diverging**
   column (an infinite interval bound: `log`'s boundary, `1/x²`) it strokes the
   **vertical column segments** instead. Measured: `1/x`/`tan` in a y∈[50,60] view are **3
   full columns** (2 before), `log(x)`'s descent measures **3.00 device px at every sampled
   row**, and a monotone `k·x` keeps one width. The band is zeroed for finite monotone
   columns (those pixels are the chord/march) and kept for oscillatory ones, where it is the
   exact column envelope. The march supplies the smooth curve's perpendicular stroke; the
   gate (`x ± R` enclosure) admits it.

`DOM` remains for the scalar march (never evaluate `log(negative)`); the interval evaluator
is authoritative for the band and the domain. Expressions with an operation the interval
backend does not implement (`floor`, `sign`, …) compile the previous sampled band + ray path
(`#define USE_INTERVAL 0`); the ray is legacy-only now.

An ordinary shallow pixel costs 1 `f` + 1 `f'` + the march (a handful of evaluations, stopped
by the escape rule after a few steps) + four interval evaluations (the pixel's column, its
stroke footprint, and the two neighbouring columns); a resolved steep pixel adds a tangent-foot
projection. The midpoint probe runs only for a wide finite column (the dependency signal); the
legacy probe and wide window are compiled out for supported expressions.

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
  setters are idempotent, and the list model reports the right roles — plus the
  `Expr`/string round-trip accounting (a string is parsed once, an expression never).
- `test_implicit.py`: the routing by free symbols (and the `ValueError` for a third symbol), the
  level-set shader text (same uniform block as the 1-D one, no sampling loop), the rendered
  geometry of a unit circle (crossings a quarter of the height from each edge, a blank middle,
  radius 1 on both axes, and a stroke of the same width at 0°/45°/90°/135°), and the two
  domain/pole guards: `x-tan(y)` has no connector at its poles or far pixels in a wide view,
  `x-log(y-1)` keeps its stroke on both sides of `y = 1`, and `y-log(x)` reaches the axis in a
  deep view.
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

All of them (macro arguments, GPU `sin`/`cos`, the fixed-x-grid sampling era's stroke
saturation, per-column phase noise, the jump-discontinuity connector) are recorded in
`AGENTS.md` as symptom → cause → fix with the measurements that settled them. After
the fixes (900×600 logical, default view): the singularity column of `sin(1/x)` is lit
over a ratio of 0.50 (= half a column filling the ±1 envelope) with flat edges, and
inside the central 41 columns no solid pixel falls outside |y|>1 (7961 before the
envelope band existed).

- Macro arguments must be parenthesized.
- GPU `sin`/`cos` are unreliable for large arguments.
- Tangent-based distance saturates on steep columns.
- Per-column sampling is phase noise for oscillating functions.
- The **join invariant** replaces pole detection: tangent-foot seeding and arc-length
  marching, so a connector of *any* origin (poles, jumps) cannot be formed
  (`AGENTS.md` item 44).

## Domain, poles and stroke (Desmos-style)

[Desmos's engineering blog](https://engineering.desmos.com/articles/press-a-key-in-the-calculator/)
spells it out: **sample** the function into points, join them into **segments**,
stroke along the segments; "break where neighbouring points suggest a jump". The same
approach, with the sampling made uniform in *arc length* (the march above):

- **Stroke = distance to the joined polyline segments** (not to the tangent), plus the
  stroked **column segments** for what the march cannot reach: a pixel also measures the
  vertical y-range of the two neighbouring columns and strokes it with the same ramp. That
  is GeoGebra's per-column segment with a vector line width, on the GPU; a steep wall
  (`1/x`/`tan` at large |y|, `log`'s descent) keeps its nominal width even though its x span
  is narrower than a pixel and its march seed is a whole view up the arc.
- **Break on jumps — by construction, not by detection**. The join invariant (a segment
  only joins samples within 2.5 px) refuses a connector of any origin: the poles of
  `1/x`/`tan(x)` and plain jump discontinuities alike (`x/sqrt(x**2)` had no pole
  denominator for the old guards to find; measured: a 454-px column at x=0). The
  interval band does not reintroduce one: a dependent quotient (`x/sqrt(x**2)`) is left to
  the march, and a sign-changing pole (`tan`, `1/x`) is a gap or a `whole` column, not a
  chord.
- **Domain** (derived by sympy, injected as `DOM` for the scalar march): denominator ≠ 0,
  `log` argument > 0, `sqrt`/even-root ≥ 0 (a negative power also excludes 0), `asin/acos`
  ∈ [−1,1], `x**(1/3)` all reals. The *interval* evaluator is authoritative for the band:
  it returns empty outside the domain, `[-∞, log h]` at a `log` boundary, and the half-open
  values at a root endpoint. A pixel just outside the boundary is painted from the stroke
  footprint (`x ± R`) and the neighbouring column segments — not by a symbol-derived ray.
  The legacy ray remains only in the fallback shader (`USE_INTERVAL 0`).
- **Undersampled columns** use the interval enclosure of the pixel's column: no ±8-column
  window, no violation count, no chord-stroke. The midpoint probe survives only as an
  **interval-dependency check** for a wide finite column (`x * 1/[a,b]` overestimation);
  the band and the neighbour column segments are always the exact interval ranges.

Measured: `sin(1/x)`'s transition overfill went from 1.66 to **0.0099 world units**
(threshold-20 pixels; the remaining edge is the stroke/AA radius); `x**(1/3)` draws both
branches; the `log(x)` descent measures **3.00 device px at every sampled row** without the
ray; `1/x`/`tan(x)` in a y∈[50,60] view are **3 full columns** (2 before the neighbour
segments); `x/sqrt(x**2)` still draws no connector (worst column 4 px); `k·x` holds one
width at every slope (the slope test at 2/10/40/200); `tan(x)` at an enormous y span fills
the screen (the interval is `whole`, matching GeoGebra); `sin(x)`/`x^2`/`exp(-x²)sin(10x)`
are unaffected.


## Performance (measured)

Startup to the first frame is about **230 ms**, attributed as follows
(Windows / D3D11 / 900×600):

| Stage | Time | Notes |
|---|---|---|
| Qt graphics device init (RHI/D3D11) | **~160 ms** | exactly what the first window's `show→expose` costs; a **bare window (no ShaderEffect) is the same**, so it is not the shader's fault |
| Window creation + QML load + first frame | ~80 ms | from the second window on (device already created) this is the order of magnitude |
| `qsb` bake | 0 / ~126 ms | cached by source hash: 0.15 ms on a hit; after a source change (code edit) it must re-bake once |
| Expression analysis (sympy) | 0.6~3 ms | `unbounded_edges` only analyses **polynomial** boundaries (`tan(x)`'s `cos(x)=0` takes 94 ms via solve+limit, while tan diverges fast and sampling is naturally deep enough, so that ray is not needed) |

Per-frame grab cycles (900×600, `grabToImage` — frame + readback, so an upper bound on
the GPU cost: the old shader measured 31 ms of pure GPU at 2400×1500, ~1/7 of that at
900×600): `sin(1/x)` default 27 ms, `tan(x)` default 34 ms, `sin(1/x)` at zoom 90
(worst case) 33 ms mean / 36 ms peak. The 1-D fragment source is about 21 KB.
Every visible curve is one full-screen fragment pass, so `Curve.visible` is the lever
when a figure carries many of them.

**Per-pixel `f` evaluations** (counter patched into the shader, mean/peak over 1-in-3
pixels; the fixed-grid renderer's numbers alongside):

| expression | fixed grid (mean/peak) | march (mean/peak) |
|---|---|---|
| `tan(x)` | 16.96 / 17 | **18.98 / 50** |
| `x^3` | 16.45 / 17 | **16.53 / 50** |
| `log(x)` | 8.50 / 33 | **7.97 / 50** |
| `1/x` | 14.64 / 33 | **6.27 / 50** |
| `exp(-x*x)*sin(10*x)` | 14.97 / 33 | **6.83 / 58** |
| `sin(1/x)` | 12.56 / 33 | **5.32 / 58** |

The mean is lower everywhere (shallow pixels cost a handful of evaluations and skip the
probe entirely); the peak is the banded column — march budget in both directions plus
the column probe — which is a small fraction of the pixels.

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
