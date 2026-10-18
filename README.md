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

For a standalone view with no injected model, the curve is explicit:

```qml
PlotView { defaultExpression: "sin(x)"; anchors.fill: parent }
```

There is no hidden default curve: the component creates its own empty `Plot` when none is
injected, and `defaultExpression` is the only way it adds one.

`PlotView` exposes `mapToScreen(x, y)` / `mapFromScreen(point)` plus the forwarders
`camera` and `curves`; input handling (pan by left-drag, wheel zoom anchored at the cursor)
is built in and switchable through `camera.panEnabled` / `camera.zoomEnabled`.

The model:

| Object | Members |
|---|---|
| `Plot` | `camera`, `curves`, `annotations`, `guides`, `xlim`/`ylim`/`aspect` (forwarded), `add_curve()` / `add_implicit()` / `remove_curve()`, `annotate()` / `add_text()` / `add_hline()` / `add_vline()`, `set_xticks()` / `set_yticks()`, `available_themes`, and the style properties: `background`, `grid`, `grid_x`, `grid_y`, `grid_color`, `grid_width`, `grid_alpha`, `grid_style`, `axis_color`, `axis_width`, `axes_position`, `color_cycle`, `line_width`, `text_color`, `tick_color`, `tick_length`, `tick_width`, `tick_pad`, `ticks_top`, `ticks_right`, `minor_ticks`, `spine_top` / `spine_bottom` / `spine_left` / `spine_right`, `font_size`, `tick_font_size`, `title`, `xlabel`, `ylabel`, `title_color`, `title_font_size`, `title_bold`, `legend`, `legend_position`, `ticks_visible`, `theme` |
| `Camera` | `centre`, `zoom` (world units per logical pixel), `aspect`, `xlim`/`ylim` (derived), `ticks_x`/`ticks_y`, `zoomStep` / `panEnabled` / `zoomEnabled`, `animationDuration`, `input` (a `ViewInput`), slots `set_viewport(w, h)` / `zoom_by(delta, u, v, w, h)` / `zoom_at(factor, u, v, w, h)` / `pan_pixels(dx, dy)` / `animate_zoom(...)` / `animate_pan(dx, dy, ms)` / `stop_animation()` / `reset()` |
| `ViewInput` | `keyboardEnabled`, `keyPanStep`, `keyPanRepeatInterval`, `panLeftShortcut` / `panRightShortcut` / `panUpShortcut` / `panDownShortcut` / `zoomInShortcut` / `zoomOutShortcut`, `matches(key, modifiers, shortcut)` |
| `Curve` | `expression` (string or sympy expression), `expr` (the parsed expression, read-only), `implicit`, `color`, `lineWidth`, `visible`, `label`, `error` (read-only), the baked `vertexShader` / `fragmentShader` |
| `CurveListModel` | the curves as a model (`curve`, `expression`, `color`, `lineWidth`, `visible`, `label`, `vertexShader`, `fragmentShader` roles) so QML puts one `ShaderEffect` behind each |
| `Annotation` | `text`, `xy` (world), `xytext` (pixel offset), `arrow`, `color`, `fontSize`, `visible` |
| `Guide` | `kind` (`"h"`/`"v"`), `value`, `color`, `width`, `style`, `visible` |

Every setter is a Qt `Property` with a notify signal, so the same calls work from QML, from a
Qt Designer slot or from a `QTimer`:

```python
QTimer.singleShot(1000, lambda: setattr(curve, "expression", "sin(3/x)"))
```

## Design notes

The design — the camera model, the Qt property/signal contract, the object model and the
build order — is specified in [`docs/api-design.md`](docs/api-design.md). In short: the
Matplotlib *vocabulary* is kept where it is vocabulary (limits, grid, title, ticks), while the
*mechanism* is refused — no `Figure`/`Axes`/`subplots` in the core (one view per widget, no
bounded page), no redraw loop (the scene is live), no toolbar and no legend (host chrome), and
an export is a canvas region at a pixel size rather than a re-rendered page (§9, implemented —
see *Export* below). A **thin compatibility facade** (`Figure`, `Axes`, `FigureCanvasQt`,
`qmlmathplot.pyplot`) provides the familiar names for porting scripts; it maps onto the same
model and refuses the mechanisms that do not fit (see *Matplotlib-style facade* below).

## Matplotlib-style facade

For porting scripts and for code that expects Matplotlib's vocabulary, the package ships a
**thin** compatibility layer. It maps onto the same `Plot`/`Camera` model and refuses the
mechanisms that do not fit an infinite, live canvas.

```python
from qmlmathplot import Figure, FigureCanvasQt

fig = Figure(figsize=(6.4, 4.8))       # stored for the window size only; no page is implied
ax = fig.gca()                         # exactly one axes per figure
ax.plot("sin(1/x)", label="sin(1/x)")
ax.set_xlim(-1, 1)
ax.set_ylim(-2, 2)
ax.set_title("sin(1/x)")
ax.set_xlabel("x")
ax.set_ylabel("y")
ax.grid(True)
ax.set_aspect("equal")

canvas = FigureCanvasQt(fig)           # a QWidget, like MathPlotWidget
layout.addWidget(canvas)
canvas.draw()                          # no-op: the Qt scene is already live
```

The same model through a pyplot-shaped module (global current figure, for scripts and
notebooks; applications should keep the object API):

```python
import qmlmathplot.pyplot as plt

plt.figure()
plt.plot("sin(1/x)")
plt.xlim(-1, 1)
plt.title("sin(1/x)")
plt.xlabel("x")
plt.grid(True)
plt.savefig("out.png")                 # Qt raster export
plt.show()                             # blocks in a script; use block=False in a host
```

What maps: one figure/one axes (`subplots()` and `subplot(111)`), `plot(expression)`,
`xlim`/`ylim`, `title`/`xlabel`/`ylabel`, `grid(axis=...)`, `aspect`, `savefig`/`to_image`,
themes, `legend`, `annotate`/`text`, `axhline`/`axvline`, `set_xticks`/`set_yticks` and
`tick_params`. `plt.gcf()`/`gca()`/`sca()`/`close()`/`show()` follow Matplotlib's
current-figure shape.

What is deliberately different:

- **One view per figure.** `subplots(2, 1)`/`add_subplot(211)` raise `NotImplementedError`;
  several panels are several `Figure` objects in a Qt layout, not a `GridSpec`.
- **Expressions, not x/y arrays.** `ax.plot("sin(x)")` is native; `ax.plot(x, y)` is refused
  because the Qt renderer evaluates an expression per pixel. For sampled data use
  `Plot.to_matplotlib()` (the hand-off), or add a data-series artist.
- **`draw()` / `draw_idle()` are no-ops.** The scene repaints from model signals; there is no
  deferred renderer or blitting.
- **`savefig` is a region at a pixel size**, not a page at a dpi. `width`/`height` are device
  pixels and `dpi` is a multiplier; `backend="matplotlib"` is the vector route.
- **Log axes and full annotation styling are not implemented.** `set_xscale()`/`set_yscale()`
  raise with the reason; annotations support text + a straight arrow + pixel offset, not
  Matplotlib's bbox/connectionstyle engine.
- **Themes are per figure**, not `rcParams`; `plt` has no global style state beyond the current
  figure.
- **`mpl_connect`/`blit` are absent.** Qt signals are the event system.

## Implicit curves

An expression whose free symbols are `x` **and** `y` is drawn as the level set `F(x, y) = 0`.
The artist follows the expression, whoever sets it: `add_curve` routes it by itself, assigning to
`curve.expression` re-routes the same curve (so a text field can turn `sin(x)` into a circle),
and `add_implicit` asks for the level-set artist explicitly — also for a function of `x` alone,
which is a pair of vertical lines. Only a *third* free symbol is refused: substitute parameters
first.

A sympy `Eq(lhs, rhs)` is always a level set: it is normalized to `lhs - rhs = 0`, and an
equation with only `x` still means the vertical lines where it holds (`Eq(x**2, 1)`), not
`y = x**2 - 1`. The canonical `expression` keeps the `Eq(...)` text and `expr` is the normalized
difference.

```python
x, y = sp.symbols("x y")
plot.add_curve("x**2 + y**2 - 1")        # a circle: {x, y} routes to the level-set artist
plot.add_curve(sp.Eq(x**2 + y**2, 1))    # the same circle in equation form
plot.add_implicit(sp.Eq(x, sp.sin(y)))   # asked for by name, equation form
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

The pixel is also a **marching-squares cell**, borrowed from GeoGebra's
`ImplicitCurveMarchingRect`/`AdaptiveQuadTree`: the four corner values are evaluated when every
corner is in the domain and no pole factor changes sign between them; the edge crossings are
interpolated and the cell's contour segment is stroked (the saddle case is paired using the cell
centre's sign). The first-order/Newton path still supplies the sub-pixel width; the cell term
adds the coverage of a thin or tangent branch whose centre projection fails. A **singular cell**
(`|Fx| + |Fy| < 1e-3` in world units, plus `|F| < 0.05`) gets a small disc, so an isolated point
such as `x**2 + y**2 = 0` is not left to the projection alone.

Still not ported: GGB's Bernstein subdivision (**polynomial-only**), its adaptive quad-tree
refinement and the `ContourAssembler`/`ContourLinker` (they exist to produce a selectable vector
path, not to rasterise). A closed loop that fits entirely inside one pixel with no corner sign
change is not seen by the four-corner test; the projection is the only fallback there. The
matplotlib hand-off skips implicit curves (a level set has no polyline to sample), while the Qt
export renders them like anything else.

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

## Keyboard, touch and animation

Clicking the plot or pressing Tab while it is in the focus chain gives it focus; while it has
focus the arrow keys pan (10% of the viewport per press by default), `+`/`=` zooms in and
`-`/`_` zooms out. Held keys advance continuously:
one `keyPanStep` per `keyPanRepeatInterval` (100 ms by default), integrated every frame, so
there is no OS key-repeat delay or granular stepping. Several arrows combine, so `Left+Up`
moves diagonally at the normal speed. Every command is a property on the camera's `input`
object, so a host can rebind it — the value is a `QKeySequence` string, and `|` separates
alternatives (empty disables the command). `input.keyPanStep`, `input.keyPanRepeatInterval`,
`input.keyboardEnabled`, `panEnabled` and `zoomEnabled` shape the behaviour the same way.

```python
camera = plot.plot.camera
input = camera.input
input.panLeftShortcut = "A"           # rebind one arrow
input.panRightShortcut = "D"
input.zoomInShortcut = "Ctrl+="
input.zoomOutShortcut = "Ctrl+-"
input.keyPanStep = 0.2                # 20% of the viewport per step
input.keyPanRepeatInterval = 80       # ms per step; smaller = faster pan/zoom
camera.animationDuration = 220        # ms; tap easing
```

Wheel and keyboard moves are **eased** (scale interpolates geometrically, so a zoom feels like
a constant rate); repeated wheel notches accumulate onto the in-flight target instead of
restarting from the last frame. Mouse drags and touch gestures are deliberately direct — the
pointer or the fingers are the animation.

Touch is built in: one finger pans, two fingers pinch to magnify and pan together, keeping the
world point under the finger centroid. They need no focus and work in both embedding modes;
like the mouse, they cancel an in-flight eased move as soon as they start.

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
have (figure size, dpi, legend, marker styles, …) are ignored. `themes.names()` lists
them (`"default"` plus the 26 sheets). An unknown name raises `KeyError` and leaves the current
theme alone.

## Axes, grid and ticks

- The **grid** is drawn in QML (a `Canvas`), one line per tick position, in `grid_color` /
  `grid_width` / `grid_alpha` / `grid_style` (`-`, `--`, `:`, `-.`).
- The **axes** are the lines through world (0,0) — the x-axis at `y = 0`, the y-axis at
  `x = 0` — drawn in `axis_color` / `axis_width`, and the **tick marks and labels ride those
  axes** instead of hugging the item's edges. When 0 is off screen the axis sticks to the
  nearest edge, so its labels are never lost (and a label never leaves the item); the grid
  stays where it is. `plot.axes_position = "edge"` keeps the labels at the edges without
  drawing axis lines.
- `plot.axes_position = "box"` draws the four Matplotlib-style **spines** around the viewport
  (same `axis_color` / `axis_width`), with ticks and labels on the bottom/left edges. It is a
  property, not a separate theme, so it composes with any theme:
  ```python
  plot.axes_position = "box"
  plot.grid = True
  ```
- Furniture is part of the same QML scene, so `to_image()` / `savefig()` capture the box,
  grid, ticks, labels and title exactly as the live view renders them (there is an export
  regression test for the four spines).
- `camera.viewport` reports the last size the view sent, so a host can size an export from it.
- The tick *values* are nice numbers computed in Python (`Camera.tick_values()` /
  `nice_ticks()`, steps of 1/2/5 x 10^n) and pushed through `ticksChanged`; QML only positions
  and formats them, so a frame never recomputes ticks.
- `plot.ticks_visible = False` hides the whole furniture (axes, marks and labels).

## Annotations, legend and guides

Annotation elements are **model-driven**, not host QML: the same objects render in the live view
and in `to_image()` / `savefig()`.

```python
plot.annotate("pole", xy=(0.0, 0.0), xytext=(24, -18), arrowprops={"color": "#333333"})
plot.text(2.0, 1.0, "f(2)=1", color="#333333", font_size=12)
plot.add_hline(0.5, color="#888888", style="--")     # horizontal guide
plot.add_vline(1.0, color="#888888", style=":")      # vertical guide

plot.add_curve("sin(x)", label="sin")
plot.add_curve("cos(x)", label="cos")
plot.legend = True
plot.legend_position = "upper right"                 # best | upper left | lower right | lower left

plot.set_xticks([-3, 0, 3], ["-3", "0", "3"])        # explicit positions + labels
plot.set_yticks([-1, 1])
plot.grid_x = True
plot.grid_y = False                                  # per-axis grid
plot.tick_width = 1.5
plot.tick_pad = 4.0
plot.ticks_top = True
plot.ticks_right = True
plot.minor_ticks = True                              # four minor marks per major interval
plot.spine_top = False                               # "box" mode only
```

`xy` is in world coordinates and `xytext` is a **logical-pixel offset** from it, so annotations
stay attached to the data while the label keeps a stable screen offset. `Annotation` and `Guide`
are also exposed as objects (`plot.annotations.at(i)`, `plot.guides.at(i)`) for per-item edits.

The Matplotlib facade forwards the same surface:

```python
ax.annotate("pole", xy=(0, 0), xytext=(24, -18), arrowprops={"color": "#333"})
ax.text(2, 1, "f(2)=1")
ax.legend(loc="lower right")
ax.set_xticks([-3, 0, 3], labels=["-3", "0", "3"])
ax.tick_params(axis="x", length=6, width=1.5, top=True)
ax.grid(True, axis="x")
ax.axhline(0.5, color="#888888")
ax.axvline(1.0, color="#888888")
```

`plt.annotate` / `plt.text` / `plt.legend` / `plt.xticks` / `plt.yticks` /
`plt.tick_params` / `plt.axhline` / `plt.axvline` forward to the current axes. The
`to_matplotlib()` hand-off maps labels, legend, annotations, guides and explicit ticks as well.

The annotation/guide/legend layers are part of the QML scene. The export path now drains the
queued Canvas paints before the synchronous readback, so the grid, annotations and guides are
present in screenshots too (previously a Canvas-painted grid could miss the grab).

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
`export.py`, `widget.py` and `canvas.py` are the View/embedding layer and do need Qt; the last
three import QtWidgets lazily, so a pure-QML host never loads it.

| Layer | File | Responsibility |
|---|---|---|
| Model | `src/qmlmathplot/model.py` | expression → GLSL, shader source template (no Qt dependency, unit-testable) |
| Camera | `src/qmlmathplot/camera.py` | `Camera`: centre / zoom / aspect, the derived ranges, pan/zoom/reset, the nice-number tick algorithm |
| Input | `src/qmlmathplot/input.py` | `ViewInput`: keyboard switch, pan step, rebindable shortcuts, key matching |
| Animation | `src/qmlmathplot/animation.py` | `CameraAnimator`: the one eased interpolator behind wheel/keyboard moves |
| Curves | `src/qmlmathplot/curve.py` | `Curve` (expression → baked `.qsb`, style, error; later edits bake on the thread pool) and `CurveListModel` |
| Plot | `src/qmlmathplot/plot.py` | `Plot`: the camera, the curves and the styling the view binds to |
| Facade | `src/qmlmathplot/figure.py` | `Figure` / `Axes`: Matplotlib-shaped names over one `Plot` (no Qt beyond the model) |
| Facade host | `src/qmlmathplot/canvas.py` | `FigureCanvasQt`: a `MathPlotWidget` attached to a `Figure` (QtWidgets, lazy) |
| Facade scripting | `src/qmlmathplot/pyplot.py` | `qmlmathplot.pyplot`: current figure, `plot`/`xlim`/`show`/`savefig` for scripts |
| View | `src/qmlmathplot/qml/PlotView.qml` (+ `PlotCurves.qml`, `PlotFurniture.qml`, `PlotInput.qml`, `view.py`) | composition: model injection, camera mapping and keyboard focus; curves, furniture and pointer input live in the three child components (one `ShaderEffect` per curve) |
| Themes | `src/qmlmathplot/themes.py` + `themes/` | the vendored Matplotlib style sheets, resolved onto the `Plot`'s properties |
| Export | `src/qmlmathplot/export.py` | `Plot.savefig()` / `to_image()`: the canvas region rendered offscreen at a pixel size (QtWidgets is imported lazily) |
| Hand-off | `src/qmlmathplot/mpl.py` | `Plot.to_matplotlib()` / `to_sympy()` / `savefig(backend="matplotlib")`: a sampled second renderer for vector output (matplotlib is an optional extra, imported lazily) |
| Bake | `src/qmlmathplot/qsb.py` | GLSL → `.qsb` (PySide6 ships `qsb.exe`), cached by source hash |
| Widget | `src/qmlmathplot/widget.py` | `MathPlotWidget`: QQuickWidget bridge, drops straight into QtWidgets layouts |
| Entry | `src/qmlmathplot/app.py` | command line / standalone window (the `qmlmathplot` script, `examples/minimal.py`) |
| Examples | `examples/` | `minimal.py` (minimal), `explorer_qtwidgets.py` (input box + widget coexistence), `explorer_qtquick.py` (Qt Quick) |

## Algorithm (fixed-x sampled polyline + the interval column)

The supported path is GeoGebra's column idea implemented on the GPU: sample the function on a
sub-pixel x grid, stroke the polyline, and fill the exact interval enclosure where a column
cannot be represented by a line.

1. **Sub-pixel polyline stroke.** Each pixel evaluates `F` at **14 samples over ±2 columns**
   (0.25 logical-px spacing). Adjacent valid samples form a polyline, and coverage is
   `R - distance to the nearest segment` — Desmos' stroke rule. The sample spacing is
   sub-pixel, so the vertices themselves are sub-pixel and the segments are diagonal: the
   edge is smooth at any zoom, not a per-column staircase. ±2 columns (rather than the old
   ±1) cover the perpendicular foot, which a steep line displaces along x by up to the
   stroke radius; measured `k·x` 3.00/3.00/3.00/2.94 device px at screen slopes
   2/10/40/200 against a nominal 3.0.
2. **Pole/jump segment guard.** A segment is skipped when the expression's pole factor
   changes sign between its endpoints (`POLE(prev_x) * POLE(xi) <= 0`) — the branches of
   `tan`/`1/x` never join across a pole — and, for a dependent quotient such as
   `x/sqrt(x**2)`, when the endpoint jump exceeds the join radius. The flat branches are
   still drawn; measured `x/sqrt(x**2)` draws its ±1 branches without the old 454-px
   connector at x = 0.
3. **Interval topology — GeoGebra's column rules on the GPU.** The expression is evaluated
   in interval arithmetic over the pixel's column (generated from sympy in `interval.py`,
   compiled to the GLSL `IVal FI(xlo, xhi)`): a conservative y-enclosure with topology
   **connected / two rays around a gap / whole / empty**. The shader reproduces the reference
   plotter's rules:
   - **connected, sub-column oscillation** (`smoothstep(1.5, 3.0, sturns)`): GeoGebra's
     `DrawInterval.drawJoined` path — the vertical join at `x.low` and the diagonal across
     the column, choosing `drawUp`/`drawDown` from the high edge; an independent column is a
     vertical at `x.high`. These are **strokes**, not a filled rectangle.
   - **divergence** (`log`'s boundary): `IntervalPath.drawNormalInfinity` →
     `leftToBottom`/`leftToTop`, only when the finite bound is on screen: one segment from
     `(x.low, finite bound)` to `(x.middle, viewport edge)`. If the interval covers the
     viewport but the finite bound is above/below it (the deep `log` view), the column is
     effectively `whole` and gets `drawWhole` (a vertical at `x.low`) — an extension needed
     because the Java branch draws nothing there although the visible curve lies inside the
     column.
   - **inverted** (`tan`/`1/x` poles): `DrawInvertedInterval` — the top segment at `x.high`
     from the viewport top to the gap's high, the bottom at `x.low` from the gap's low to the
     viewport bottom; no filled ray.
   - **whole**: `DrawInterval.drawWhole` — one vertical segment at `x.low`.
   Deliberate deviations, because this is a fragment shader and not the Java tuple list: the
   14-sample polyline supersamples GGB's one interval per column for resolved stretches; a
   dependent quotient's whole column is still suppressed (the fixed-x polyline draws the real
   branches, avoiding the `x/sqrt(x**2)` connector); and a joined inverted column uses those
   sample branches instead of replaying `JoinLines.inverted`'s neighbour decisions.
4. **Domain.** `DOM`/`DOMS` (from sympy) guard every `F` evaluation, so `log(negative)` is
   never evaluated; a pixel just outside the domain still gets the band, which is centred
   on the boundary.

An ordinary pixel costs **14 `F` evaluations** plus two interval evaluations; there is no
march, no tangent-foot projection and no column probe on the supported path. Expressions
with an operation the interval backend does not implement (`floor`, `sign`, …) compile the
legacy sampled band + ray + march path (`#define USE_INTERVAL 0`); it is the only user of
the arc-length march now.

## Tests

```
uv run pytest                      # everything
uv run pytest -m "not gui"         # skip the render smoke tests that open a window
QSG_RHI_BACKEND=opengl uv run pytest -m gui
uv run ruff check src tests examples   # bug-level lint (undefined names, unused imports)
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
- `test_overlay.py`: annotations (`annotate`/`text`), horizontal/vertical guides, legend and
  custom ticks, the `Axes`/`pyplot` forwarding, and the matplotlib hand-off mapping.
- `test_qsb_cache.py`: the cache location/info/clear API and the age + LRU prune; unrelated
  files in the temp directory are not touched.
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
  fixed aspect being undistorted by the widget's shape, and Tab reaching the plot and leaving
  it again. `conftest.py` provides a session-scoped `QApplication` (QtWidgets
  needs one, and a process may only have one).
- `test_qtquick_coexistence.py`: the same drag conflict in Qt Quick (a `Flickable`
  must not steal the pan), plus Tab focus entering and leaving the QML plot.
- `test_handoff.py`: the matplotlib/sympy hand-off — the figure reflects the limits, aspect,
  `adjustable`, grid, title, spines at zero and per-curve style; the sampling is uniform and NaN
  outside the domain (so `log(x)` breaks); `savefig(backend="matplotlib")` writes real `.svg`,
  `.pdf` and `.png`; `to_sympy().save()` writes a file and keeps per-curve colours; importing
  `qmlmathplot` does not import matplotlib, and hiding matplotlib produces an error naming the
  extra.
- `test_async_bake.py`: a later expression assignment bakes on the thread pool, a stale bake
  is discarded, and a failed bake keeps the drawn curve.
- `test_compat.py`: the Matplotlib-shaped facade — expressions (and the refusal of x/y arrays),
  limits/title/labels/grid/aspect, the single-axes rule, `pyplot` current-figure semantics,
  `FigureCanvasQt` rendering and export, and that importing `qmlmathplot` does not load
  QtWidgets.
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
| `qsb` bake | 0 / ~126 ms | cached by source hash: 0.15 ms on a hit; later `expression` assignments bake on Qt's thread pool, so only the first curve construction blocks (tests force the synchronous path) |
| Expression analysis (sympy) | 0.6~3 ms | `unbounded_edges` only analyses **polynomial** boundaries (`tan(x)`'s `cos(x)=0` takes 94 ms via solve+limit, while tan diverges fast and sampling is naturally deep enough, so that ray is not needed) |

Per-frame grab cycles (900×600, `grabToImage` — frame + readback, so an upper bound on
the GPU cost), steady state on the same window: `sin(1/x)` default **13.6 ms**, `tan(x)`
default **12.4 ms**, `sin(1/x)` at zoom 90 (worst case) **12.5 ms**. The previous
march/interval renderer measured 27/34/33 ms on the same machine: 14 samples are cheaper
than the march's seed + tangent projection + walk + probe, and the march is now compiled
only for the legacy path. The 1-D fragment source is about 21 KB.

**Per-pixel `F` evaluations.** The supported path uses 14 samples for the polyline in the live
view and in exports; two interval evaluations cover the column and the stroke footprint. There
is no march budget and no band-window evaluation. Anti-aliasing comes from the analytic
coverage ramp, not from the sample count; the samples only approximate the polyline. Sampling
was tuned by the render tests: 8 samples overfills the `sin(1/x)` transition and breaks the
`log` descent, 12 still overfills slightly, 14 passes.

An interval early-out over ±2 columns rejects background pixels before the sample loop: if the
pixel is farther than the stroke radius from the curve's y range in that window, it returns
transparent without evaluating `F`. Measured by alternating two live 900×600 views (15 pans
each, `grabToImage`, including readback): `sin(x)` 23.7 → 22.2 ms, `sin(1/x)` 23.6 → 21.0 ms,
`x**2` 22.7 → 20.4 ms. Whole/infinite columns keep the full path, so `tan`/`1/x` near a pole
are unchanged.

## Shader cache

`qsb` binaries are cached in the platform temp directory:

```python
import qmlmathplot

qmlmathplot.cache_dir()    # e.g. C:\Users\me\AppData\Local\Temp\qmlmathplot-qsb
qmlmathplot.cache_info()   # {'path': ..., 'files': ..., 'bytes': ...}
qmlmathplot.clear_cache()  # remove this package's entries, returns bytes freed
```

The location is `tempfile.gettempdir()/qmlmathplot-qsb`: `%TEMP%` on Windows, the per-user
`/var/folders/.../T` on macOS, and `$TMPDIR` or `/tmp` on Linux. Set
`QMLMATHPLOT_QSB_CACHE` to use another directory.

**It is not left to grow without bound.** The first bake in a process, and at most once per
minute afterwards, runs `prune_cache()`:

- entries older than **30 days** are removed;
- if the cache is over **32 MiB** (default), the least-recently-used `.qsb` files are removed
  until it is at 75% of the cap — so an existing 60 MB cache drops to about 24 MB on the next
  bake;
- interrupted-bake leftovers (`*.qsb.tmp`, `*.vert`, `*.frag`) older than an hour are removed.

Both knobs are overridable: `QMLMATHPLOT_QSB_CACHE_MB` (size cap) and
`qmlmathplot.prune_cache(max_bytes=..., max_age_days=...)` for an explicit pass. The cache
contains compiled shaders only; the GLSL source and temporary outputs are deleted after each
bake. Concurrent processes share the directory safely: writes use unique temp names and an
atomic replace, and pruning only removes entries by age/LRU.

On macOS/Linux `qsb` is looked up in the PySide6 package (`Qt/bin`, `Qt/libexec`) and on
`PATH`; if the wheel does not ship it, install Qt Shader Tools (`qt6-shadertools` on most
distributions) or add `qsb` to `PATH`. Only the Windows wheel has been confirmed to ship it.

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
- Screen-space furniture is QML: background `Rectangle`, grid `Canvas`, axes/ticks
  `Rectangle`s, labels/title `Text`; curves are `ShaderEffect` passes. Snap its lines to
  **device** pixels with an integral device width (`_deviceWidth`/`_snapLine`), not logical
  half-pixels: the 1.07-logical axis is 2 device px at DPR 1.5 and otherwise alternated
  between one and two rows as it drifted with the zoom.
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
