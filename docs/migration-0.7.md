# Migration to 0.7

0.7 is a breaking release. There are no compatibility shims: the 0.6 process client,
`Plot.calculator`, `Curve.expr`, `Plot.to_sympy` and the QML `defaultExpression` are gone.
This page lists the replacements.

## At a glance

| 0.5 / 0.6 | 0.7 |
|---|---|
| `Plot()` parsed text in-process | `Plot(program_source=SymPyProgramSource())`; no source means `ProgramSourceMissingError` |
| `Plot.calculator = InlineCalculator()` / `ProcessCalculator` | produce a `Program` in a worker and call `plot.add_program(program)`; `qmlmathplot.client` was removed |
| `curve.expr` (sympy) | `curve.program` (`Program`); `curve.program.text` for the text |
| `Plot.to_sympy()` | removed; `Plot.to_matplotlib()` samples `Program.numpy` |
| `add_curve(sp.sin(x))` | still accepted (converted to text), but text needs a source |
| `cpuRenderer: true` (QML) | `renderMode: "cpu"` (also `Plot.renderMode`, `MathPlotWidget(render_mode=…)`, `--render-mode`) |
| `import QmlMathPlot 1.0` | `import QmlMathPlot 2.0` |
| `defaultExpression` on `PlotView` | removed: a QML-created `Plot` has no source; inject the model from Python |
| `Figure` / `Axes` / `FigureCanvasQt` / `qmlmathplot.pyplot` | removed (0.6 already); use `Plot` / `Camera` / `PlotView` |
| `pyplot`, `Plot.to_sympy`, `Curve.expr` | removed |

## Text curves need a source

```python
# 0.6
plot = Plot()
plot.add_curve("sin(x)")

# 0.7
from qmlmathplot.calc import SymPyProgramSource
plot = Plot(program_source=SymPyProgramSource())   # [calc] extra
plot.add_curve("sin(x)")
```

The `Program` is returned synchronously; only the shader bake is asynchronous:

```python
curve = plot.add_curve("sin(x)")
assert curve.program is not None          # immediately available
assert curve.computeShader.toString() == ""  # still baking
# later: curve.shadersChanged / curve.error
```

`add_curve("a*x")` no longer raises: the parameter error is reported on `curve.error` (and
`plot.plotError`).

## Program-first hosts

If the host already has a worker (Symplify's case), the UI process does not need SymPy or a
process client:

```python
program = worker.request("sin(1/x)")     # a Program from any source
plot = Plot(program_source=None)
plot.add_program(program)
```

Data curves never needed a parser:

```python
plot.add_points(x, y)                    # synchronous, plain data
```

See [Programs and calculators](programs.md) for the wire format and
`examples/process_calculator.py` for a complete worker round trip.

## QML

- `import QmlMathPlot 2.0`.
- `PlotView { plot: backendPlot }` — the model must be created in Python.
- `renderMode` mirrors `plot.renderMode`; `"cpu"` runs on Qt Quick's software backend
  (`QT_QUICK_BACKEND=software` before `QGuiApplication`).
- `defaultExpression` is gone; a host that wants a startup curve adds it to its own `Plot`.

## Extras

- Core: `PySide6`, `numpy`. No SymPy at import time.
- `[calc]`: `sympy` — `SymPyProgramSource` and `python -m qmlmathplot.calc`.
- `[matplotlib]` / `[mpl]`: `matplotlib` only; the hand-off samples `Program.numpy`.

## Behavior notes

- A failed parse or bake keeps the previous drawing and sets `curve.error`; nothing is
  thrown into QML.
- `Plot.to_image()` still requires a `QApplication` (the offscreen path uses
  `QQuickWidget`); QtWidgets is imported lazily.
- `renderMode="cpu"` covers 1-D curves and QML furniture; implicit curves show an explicit
  message, data/numpy-only curves are not drawn yet (see
  [Rendering modes](rendering.md)).
