# API design — a Matplotlib-flavoured surface on Qt

Status: **decided** (this document overrides the earlier `aspect` design where it says so).
Audience: whoever adds multiple curves, axes, grid, annotations and custom elements next.

---

## 1. Principles

1. **Qt is the runtime, not a wrapper.** Every knob is a Qt `Property` with a `notify`
   signal; every collection is a `QAbstractListModel`; every action is a `Slot`. Changing a
   property emits exactly one signal and the QML re-renders — no Python call is needed to
   "update the plot", and nothing is mutated behind Qt's back.
2. **Expressions, not data.** A curve is `y = f(x)` evaluated *per pixel in a fragment
   shader*, not a polyline sampled on the CPU. This is the fundamental difference from
   Matplotlib and it drives everything below: an artist is a shader, "many curves" means
   several full-screen passes, and the cost is proportional to pixels × curves, never to
   the function's frequency.
3. **One world→screen transform, owned by the Axes.** Curves, grid, ticks, labels,
   annotations and exported images all read the same limits. Nothing keeps a private copy.
4. **The GPU draws curves and the grid; QML draws text and vector furniture** (ticks,
   labels, legend, annotations). Text in a shader is a dead end.
5. **Offscreen rendering is Qt's job** (`QQuickRenderControl`), not a second renderer: the
   export path instantiates the *same* QML component at the requested size, so what you
   export is what you see.

## 2. Object model

```mermaid
graph TD
    F[PlotFigure] -->|axes| A[PlotAxes]
    F -->|curves| C["CurveListModel (QAbstractListModel)"]
    F -->|annotations| N["AnnotationListModel"]
    F -->|legend / export settings| S[PlotFigure properties]
    A -->|xlim, ylim, aspect, ticks, grid, title, labels| A
    C --> C1["Curve (QObject): expression, color, lineWidth, visible, label, error"]
    N --> N1["Annotation (QObject): text, xy, xytext, arrow, color"]
    V["PlotView.qml"] -->|binds| F
    V --> V1["grid ShaderEffect (static, baked once)"]
    V --> V2["Repeater over curves -> one ShaderEffect per curve"]
    V --> V3["Canvas/Text: frame, ticks, labels, legend"]
    V --> V4["Repeater over annotations -> world-anchored Items"]
    V --> V5["underlay / overlay default properties (arbitrary QML)"]
```

`PlotFigure` is a plain `QObject` (no QWidget, no window): the same figure can be shown in
a `QQuickView`, embedded with `MathPlotWidget`, or rendered offscreen for an export. The
model owns state; `PlotView.qml` owns pixels.

## 3. Python API (the Matplotlib-flavoured surface)

```python
from qmlmathplot import PlotFigure

fig = PlotFigure()                       # owns one PlotAxes; more later (subplots)

ax = fig.axes
ax.xlim = (-6.0, 6.0)                    # visible limits (world units)
ax.ylim = (-2.0, 2.0)
ax.aspect = "auto"                       # "auto" | 1.0 | 2.0 ...  (matplotlib convention)
ax.grid = True
ax.grid_color = "#2a2a3a"
ax.title = "sin(1/x)"
ax.xlabel, ax.ylabel = "x", "y"
ax.ticks = "auto"                        # "auto" | [(pos, "label"), ...] | None
ax.legend = True                         # uses Curve.label

c1 = fig.add_curve("sin(1/x)", color="#33ccff", line_width=1.5, label="sin(1/x)")
c2 = fig.add_curve("tan(x)", color="#ff8866", label="tan")
c1.expression = "sin(2/x)"               # one signal -> re-bake -> QML swaps the shader
c2.visible = False
fig.remove_curve(c2)

ann = fig.annotate("pole", xy=(0.0, 0.0), xytext=(24, -18), arrow=True)

fig.savefig("out.png", xlim=(-1, 1), ylim=(-1, 1), width=1200, height=400)
img = fig.to_image(width=800, height=600)          # -> QImage, same pipeline

# plt-flavoured one-liner for scripts/notebooks (module-level convenience, no global state):
qmlmathplot.quickplot("sin(1/x)", xlim=(-1, 1), ylim=(-1, 1))
```

Every setter above is a `Property` with a `notify` signal, so the same calls work from QML,
from a Qt Designer slot, or from a `QTimer` at runtime:

```python
QTimer.singleShot(1000, lambda: setattr(c1, "expression", "sin(3/x)"))
```

## 4. QML surface

```qml
import QmlMathPlot 1.0

PlotView {
    figure: myFigure                 // inject the model (or let it create one)
    anchors.fill: parent
    underlay: Item { }               // drawn below the curves, world-anchored helpers below
    overlay: Item { }                // drawn above everything
}
```

`PlotView` provides `mapToScreen(x, y) -> point` and `mapFromScreen(point) -> {x, y}` so
custom QML elements can sit at world coordinates without re-deriving the transform.

## 5. The Qt contract (what makes runtime changes free)

| Object | Property | Type | Notify | Effect when it changes |
|---|---|---|---|---|
| `PlotFigure` | `axes` | `PlotAxes*` | `axesChanged` | rebind the whole view |
| | `curves` | `CurveListModel*` | model signals | `Repeater` adds/removes one ShaderEffect |
| | `annotations` | `AnnotationListModel*` | model signals | `Repeater` adds/removes one Item |
| | `legend` | `bool` | `legendChanged` | legend box shown/hidden |
| `PlotAxes` | `xlim`, `ylim` | `QVector2D` | `limitsChanged` | grid, ticks, labels, all curves, all annotations |
| | `aspect` | `QVariant` (`"auto"` or number) | `aspectChanged` | the effective limits (see §7) |
| | `grid`, `gridColor`, `gridWidth` | `bool`, `QColor`, `double` | `gridChanged` | the grid shader's uniforms only |
| | `ticks` | `QVariant` | `ticksChanged` | tick positions + labels |
| | `title`, `xlabel`, `ylabel` | `QString` | `labelsChanged` | text items |
| `Curve` | `expression` | `QString` | `expressionChanged` → `shadersChanged` | re-bake (cached), swap `.qsb` |
| | `color`, `lineWidth` | `QColor`, `double` | `styleChanged` | shader uniforms |
| | `visible`, `label` | `bool`, `QString` | `visibleChanged`, `labelChanged` | Repeater visibility / legend |
| | `error` | `QString` | `errorChanged` | red text; the previous shader stays |
| `Annotation` | `text`, `xy`, `xytext`, `arrow`, `color` | … | `changed` | that one item |

Rules:
* Setters are idempotent and emit only on an actual change (no signal storms).
* A failed expression keeps the last working shader and fills `error` (never a blank plot).
* The bake is cached by source hash, so re-setting the same expression is free; a new
  expression is baked on a worker and the swap happens when `shadersChanged` fires, so the
  GUI thread never blocks on `qsb`.

## 6. Curves (many of them)

* One `ShaderEffect` per curve, produced by a `Repeater` over `CurveListModel`. Each pass is
  independent: its own expression, colour, width, visibility, and its own bake.
* Cost: **one full-screen fragment pass per visible curve** (the per-pixel work of §"cost"
  in the README, times the number of curves). The existing per-pixel gating keeps each pass
  cheap where nothing is drawn, but the pass itself is not free — so `visible` is the
  intended lever, and a future "merge N curves into one generated shader" variant is the
  documented optimisation if a figure needs many curves at once (it costs one re-bake per
  curve-count change and a fixed maximum N).
* Data series (`plot(x, y)` with arrays, like Matplotlib) are a *different* artist: a QML
  `Shape`/`Canvas` polyline, not a shader. The API reserves `fig.add_series(x, y)` for it so
  the expression-based `add_curve` never has to pretend to be a data plot.

## 7. Axes, ticks, grid, labels

* Limits live in `PlotAxes` and are what pan/zoom mutate (this replaces today's `ViewRect`).
* `aspect = "auto"` keeps today's default: the limits map straight onto the item, so the
  shape follows the widget. A number keeps the y-unit/x-unit pixel ratio fixed
  (matplotlib's convention, `1.0` = square units) by **expanding the limits around their
  centre — never cropping** — so the plot keeps filling its area.
* With a numeric aspect, a resize keeps the **scale** (world units per pixel) and lets the
  limits follow the widget; only the first size report sets the baseline. This is the
  decision from the previous round, kept deliberately: resizing must not zoom the curve.
* Tick *values* are computed in Python (`PlotAxes.tick_values()` — nice-number algorithm,
  pure and unit-tested) and pushed through `ticksChanged`; QML only positions and formats
  them. Labels are regenerated on limit changes, not per frame.
* The grid is a **static shader** (tick positions passed as uniforms): crisp at any zoom,
  no per-frame QML churn, baked once. Tick marks and labels are QML.

## 8. Annotations and custom elements

* `fig.annotate(text, xy=..., xytext=..., arrow=...)` — `xy` in world units, `xytext` an
  offset in pixels, so the label does not move when the view zooms.
* Anything else is QML: `PlotView.underlay` / `.overlay` are default properties, and
  `mapToScreen()` gives the transform. This is the escape hatch — arbitrary QML inside the
  plot's coordinate space, with no Python model needed.
* The legend is generated from `Curve.label` + `Curve.color`; no per-curve QML.

## 9. Export / screenshots

```python
fig.savefig("out.png", xlim=(-1, 1), ylim=(-1, 1), width=1200, height=400, dpi=1.0)
fig.to_image(width=800, height=600)          # -> QImage
```

Semantics (this is the part that differs from Matplotlib on purpose):

* `xlim` / `ylim` default to the axes' current limits; `width` / `height` default to the
  live view's size; `dpi` scales the pixel size for high-resolution output.
* **The requested range is framed and stretched onto the requested pixel size.** An export
  of `xlim=(-1,1), ylim=(-1,1)` at `1200×400` is 4:1 — the export is a *report figure*, not
  a window, and the aspect setting does not silently letterbox it. If square units are
  wanted, pass a size with the range's ratio (or set `ax.aspect = 1.0` and let the export
  stretch, which is the same thing for a matching ratio).
* Implementation (**verified**, see below): a hidden `QQuickWidget` — `WA_DontShowOnScreen`
  + `setResizeMode(SizeRootObjectToView)` + `resize(width, height)` + `show()` — with the
  figure attached and the requested limits set, read back with
  **`QQuickWidget.grabFramebuffer()`** (synchronous, exact size, no visible window).
  Verified: `sin(1/x)` with `xlim=ylim=(-1,1)` at 800×200 logical produced a 1200×300
  image (DPR 1.5) with the curve stretched 4:1.
  Two dead ends, recorded so they are not retried: `QQuickRenderControl.grab()` is not
  exposed by PySide6 6.11 (and reading the RHI target by hand is impossible — `QRhi` is not
  bound), and `QQuickItem.grabToImage()` returns a **null** result on a window that was
  never exposed (`WA_DontShowOnScreen`), so it cannot be used for a hidden export.
* `transparent=True` clears to alpha 0 (useful for slides); everything else follows the
  live styling, so an export can never drift from what the user sees.

## 10. Where we deliberately differ from Matplotlib

| Matplotlib | Here | Why |
|---|---|---|
| `plt.*` global current-figure state | explicit `PlotFigure` objects (+ `quickplot()` for scripts) | Qt apps have many figures; global state fights QML |
| artist list redrawn per figure | one QML item per curve, driven by model signals | no redraw loop; Qt owns invalidation |
| `plot(x, y)` with data arrays | `add_curve("f(x)")` (shader) / `add_series(x, y)` (QML geometry) | the renderer is per-pixel expression evaluation |
| transforms stack (data→axes→figure→display) | one world→item mapping on `PlotAxes` | only one coordinate space exists |
| `savefig(dpi=...)` re-renders the figure | offscreen QML render at the requested size, range stretched | the figure *is* a Qt scene |
| blocking `show()` | the widget/`PlotView` is a live item; `quickplot()` blocks | Qt's event loop is the app's |
| `ax.set_*` then `draw()` | properties with notify signals | Signal & Slot, no explicit draw |

## 11. Migration from today's code

| Today | Becomes |
|---|---|
| `PlotController` (expression + shaders + view) | `Curve` (expression + shaders + style) and `PlotAxes` (view) |
| `ViewRect` | `PlotAxes` limits (+ the same pan/zoom math, kept) |
| `PlotController.view` (QVector4D) | `PlotAxes.effective_limits` (the drawn limits, after the aspect expansion) |
| `MathPlot.qml` | `PlotView.qml` (grid shader + Repeater + furniture) |
| `MathPlotWidget` | unchanged public shape, now backed by `PlotFigure` |
| `PlotController.setViewport` | `PlotAxes.set_viewport` (same scale-preserving rule) |

Backwards compatibility: `PlotController` and `MathPlot` stay as thin aliases for one
release, marked deprecated in their docstrings; the explorers move to `PlotView` so the new
path is the one that is exercised.

## 12. Build order

1. `PlotAxes` (limits/aspect/ticks) + `Curve` split out of `PlotController`; `PlotView.qml`
   renders one curve. No visual change; all existing tests keep passing.
2. `CurveListModel` + `Repeater` → multiple curves, legend, per-curve visibility.
3. Grid shader + ticks + labels + title.
4. `AnnotationListModel` + `underlay`/`overlay` + `mapToScreen`.
5. Offscreen exporter (`to_image` / `savefig`) + its tests (size, range, stretch, transparency).
6. Deprecation shims removed.

Each step is independently shippable and testable; steps 1–2 are the ones that change
existing files, the rest are additive.
