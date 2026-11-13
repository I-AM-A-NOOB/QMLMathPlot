# API reference

This is the reference for the 0.7 API. Python names are `snake_case`; Qt property names are
`camelCase` and work from QML. Every Qt property has a notify signal named `<name>Changed`
unless noted.

## Installation and extras

```bash
pip install qmlmathplot                 # core: PySide6 + numpy
pip install "qmlmathplot[calc]"         # SymPyProgramSource, python -m qmlmathplot.calc
pip install "qmlmathplot[matplotlib]"   # to_matplotlib / savefig(backend="matplotlib")
```

The core does not import SymPy. Without a `ProgramSource`, text curves report
`ProgramSourceMissingError`; program and data curves work regardless.

## `ProgramSource` and `Program`

`qmlmathplot.protocol`:

```python
class ProgramSource(Protocol):
    def program(self, text: str, *, implicit: bool | None = None) -> Program: ...

class ProgramSourceMissingError(RuntimeError): ...
```

`qmlmathplot.calc.SymPyProgramSource()` is the bundled implementation (needs the `[calc]`
extra). It is synchronous: the returned `Program` is available when the call returns.

`Program` (frozen dataclass, `qmlmathplot.protocol`):

| field | type | meaning |
|---|---|---|
| `text` | `str` | canonical input |
| `kind` | `str` | `"intervals"`, `"adaptive"`, `"implicit"`, `"data"` |
| `args` | `tuple[str, ...]` | `("x",)` or `("x", "y")` |
| `glsl` | `dict[str, str]` | `{"compute": …}` or `{"fragment": …}` |
| `math` | `str or None` | `def f(x): …` using the `math` module |
| `numpy` | `str or None` | `def f(x): …` using `numpy` |
| `error` | `str` | non-empty when the expression cannot be drawn |

Methods: `to_dict()`, `from_dict()`, `to_json()`, `from_json()`, `compile(numpy=False)`
(returns the compiled function; the namespace has `__builtins__ = {}`), `with_error()`.
See [Programs and calculators](programs.md) for the wire format.

## `Plot`

```python
Plot(parent=None, *, program_source: ProgramSource | None = None)
```

`Plot()` has no source; `add_curve("…")` then raises `ProgramSourceMissingError`.
`add_program()` and `add_points()` never need a source.

### Properties

| name | type | notes |
|---|---|---|
| `program_source` | `ProgramSource or None` | plain Python property; setting it re-resolves existing text curves |
| `renderMode` | `"gpu"` / `"cpu"` | curve renderer; invalid values raise `ValueError` |
| `plotError` | `str` (read-only) | first non-empty curve error |
| `camera` | `Camera` | where you look |
| `curves` | `CurveListModel` | the curves |
| `annotations`, `guides` | list models | text/arrow annotations, h/v guides |
| `xlim`, `ylim` | pair (forwarded to `camera`) | visible range |
| `aspect` | `"auto"` or a number (forwarded) | y/x scale ratio |

Style properties (all have notify signals; themes write them):

`background`, `grid`, `grid_x`, `grid_y`, `grid_color`, `grid_width`, `grid_alpha`,
`grid_style`, `axis_color`, `axis_width`, `axes_position`, `color_cycle`, `line_width`,
`text_color`, `tick_color`, `tick_length`, `tick_width`, `tick_pad`, `ticks_top`,
`ticks_right`, `minor_ticks`, `spine_top`, `spine_bottom`, `spine_left`, `spine_right`,
`font_size`, `tick_font_size`, `title`, `xlabel`, `ylabel`, `title_color`,
`title_font_size`, `title_bold`, `legend`, `legend_position`.

`axes_position` is `"zero"` (axes through the origin), `"edge"` (ticks at the item edge,
no axis line) or `"box"` (four spines).

`theme` applies a named style sheet; `available_themes` lists the names. Assigning a theme
always re-applies it (so `theme = "default"` is a reset); an unknown name raises `KeyError`
and changes nothing.

### Curve methods

```python
add_curve(expression: str | sympy.Basic, color=None, line_width=None, label="") -> Curve
add_program(program: Program, color=None, line_width=None, label="") -> Curve
add_points(x, y, color=None, line_width=None, label="") -> Curve
add_implicit(expression, color=None, line_width=None, label="") -> Curve
remove_curve(curve) -> None
```

- `add_curve` uses the plot's `program_source`; the `Program` is ready when it returns. The
  shaders bake asynchronously. Exceptions from the source are reported on `curve.error`;
  a missing source raises.
- `add_program` is the decoupled entry point: no parsing, no SymPy, no source needed.
- `add_points` draws a polyline through the pairs; numpy arrays work; `NaN`/`inf` break the
  line. Data curves are always synchronous.
- `add_implicit` forces the level-set renderer regardless of free symbols.

### Annotations and guides

```python
add_annotation(text, xy, xytext=(0, 0), arrow=False, color=None, font_size=None)
add_text(x, y, text, **kwargs)
annotate(text, xy, xytext=(24, -18), arrowprops=None, **kwargs)   # matplotlib-style
remove_annotation(annotation); clear_annotations()

add_hline(y, color=None, width=1.0, style="--"); add_vline(x, ...)
remove_guide(guide); clear_guides()
```

`xy` is world coordinates; `xytext` is a logical-pixel offset. `Annotation` has `text`,
`xy`, `xytext`, `arrow`, `color`, `fontSize`, `visible`; `Guide` has `kind`, `value`,
`color`, `width`, `style`, `visible`.

### Ticks

```python
set_xticks(values, labels=None); set_yticks(values, labels=None)
clear_xticks(); clear_yticks()
xticks; yticks     # [[value, label], ...] (lists, not tuples: QML-safe)
```

### Export

```python
to_image(*, xlim=None, ylim=None, width=None, height=None, dpi=1.0,
         adjustable=None, transparent=False) -> QImage
savefig(path, *, backend="qt", **kwargs)
to_matplotlib(*, xlim=None, ylim=None, samples=2000, adjustable="box")
```

- `to_image` renders the canvas region offscreen in device pixels; needs a `QApplication`
  (QtWidgets is imported lazily) and raises a clear error in a pure-QML host.
  `width`/`height` default to the live device size; `dpi` multiplies them. `adjustable` is
  `"box"`, `"datalim"` or `"stretch"`.
- `savefig` uses the Qt raster export (`backend="qt"`) or the sampled matplotlib hand-off
  (`backend="matplotlib"`; `.svg`/`.pdf` work, needs the `[matplotlib]` extra).
- `to_matplotlib` samples each curve's `Program.numpy`; implicit curves are skipped (a
  polyline cannot represent a level set). No SymPy is involved.

## `Camera`

| name | meaning |
|---|---|
| `centre` | world point at the item centre |
| `zoom` | world units per logical pixel on x |
| `aspect` | `"auto"` or y/x scale ratio |
| `scale_x`, `scale_y` | world units per logical pixel (derived) |
| `xlim`, `ylim` | visible range (derived from centre/scale/size) |
| `viewport` | last reported viewport size |
| `ticks_x`, `ticks_y` | `[[value, label], ...]` |
| `zoomStep`, `panEnabled`, `zoomEnabled`, `animationDuration` | input/animation knobs |
| `input` | `ViewInput` (keyboard shortcuts) |

Methods: `set_view(cx, cy, sx, sy)`, `set_viewport(w, h)`, `zoom_by(delta, u, v, w, h)`,
`zoom_at(factor, u, v, w, h)`, `animate_zoom(delta, u, v, w, h, duration)`,
`animate_pan(dx, dy, duration)`, `pan_pixels(dx, dy)`, `stop_animation()`, `reset()`.
`u, v` are fractions of the viewport (0.5, 0.5 = centre).

`ViewInput`: `keyboardEnabled`, `keyPanStep`, `keyPanRepeatInterval`,
`panLeftShortcut` / `panRightShortcut` / `panUpShortcut` / `panDownShortcut` /
`zoomInShortcut` / `zoomOutShortcut` (strings; empty disables a command),
`matches(key, modifiers, shortcut)`.

## `Curve`

Created by `Plot.add_curve`/`add_program`/`add_points`; usually not constructed directly.

| name | meaning |
|---|---|
| `expression` | the text (assignment re-resolves it through the source) |
| `program` | the current `Program` (read-only; available synchronously) |
| `set_program(program)` | apply a program built elsewhere |
| `set_program_source(source)` | switch the source and re-resolve the text |
| `implicit` | true for a level set |
| `is_data`, `points` | data curves: the `(x, y)` tuples |
| `mathSource` | the current `Program.math` (for the CPU fallback) |
| `color`, `lineWidth`, `visible`, `label` | style |
| `error` | parse/bake error (read-only); the previous drawing stays |
| `vertexShader`, `fragmentShader`, `computeShader` | baked `.qsb` URLs (read-only) |

Signals: `expressionChanged`, `exprChanged` (program applied), `styleChanged`,
`visibleChanged`, `labelChanged`, `errorChanged`, `shadersChanged`.

## `CurveListModel`

Roles: `curve`, `expression`, `color`, `lineWidth`, `visible`, `vertexShader`,
`fragmentShader`, `label`, `mathSource`, `implicit`.

Methods: `add_curve`, `add_program`, `add_points`, `add_implicit`, `remove_curve(curve)`,
`at(row)`, `index_of(curve)`, `rowCount()`, `len()`; `set_program_source(source)`.
`add_curve` and friends are QML-callable slots.

## `MathPlotWidget`

```python
MathPlotWidget(expression="sin(x)", parent=None, *,
               plot=None, line_width=None, curve_color=None, background_color=None,
               aspect="auto", program_source=None, render_mode="gpu")
```

Requires a `QApplication`. With `program_source=None` and a text expression, the
constructor raises `ProgramSourceMissingError`. Pass `plot=` to host an existing model.

Properties/methods: `plot`, `curve`, `expression`, `error`, `line_width`, `curve_color`,
`background_color`, `aspect`, `view_bounds()`, `reset_view()`, `zoom(delta, u=0.5, v=0.5)`,
`pan_pixels(dx, dy)`. Signals: `expressionChanged`, `errorChanged`.

## QML `PlotView`

```qml
import QmlMathPlot 2.0
PlotView {
    plot: backendPlot          // a Plot created in Python (required for text curves)
    renderMode: "gpu"          // mirrors plot.renderMode; "cpu" for software backends
    anchors.fill: parent
}
```

Properties: `plot`, `camera` (forwarder), `curves` (forwarder), `renderMode`.
Functions: `mapToScreen(x, y)` (world → item logical pixels), `mapFromScreen(point)`.
Input: left-drag pans, wheel zooms at the cursor, arrow/zoom keys work while focused; all
are switchable through `camera.panEnabled` / `camera.zoomEnabled` / `camera.input`.

A QML-created `Plot` (no Python injection) has no `ProgramSource`; the component therefore
never adds a curve by itself.

## Command line

```
qmlmathplot [expression] [--backend d3d11|d3d12|vulkan|metal|opengl|null]
            [--render-mode gpu|cpu] [--software] [--width N] [--height N]
```

`--software` sets `QT_QUICK_BACKEND=software` before the application starts and implies
`--render-mode cpu`.
