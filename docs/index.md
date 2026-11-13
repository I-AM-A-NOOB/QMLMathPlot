# QMLMathPlot documentation

QMLMathPlot is an embeddable function plotter for Qt Quick and QtWidgets. The canvas is
infinite and camera-driven: a `Plot` owns a `Camera` (where you look) and a list of `Curve`s
(what is drawn), and `PlotView` is the QML item that draws them.

## Start here

| I want to… | Read |
|---|---|
| understand how the pieces fit together | [Architecture](architecture.md) |
| look up a property, method or QML name | [API reference](api.md) |
| build the expressions myself (a worker, another language, a notebook) | [Programs and calculators](programs.md) |
| choose the GPU or CPU renderer, deploy without a GPU | [Rendering modes](rendering.md) |
| move code from 0.5 / 0.6 | [Migration to 0.7](migration-0.7.md) |
| read the original design rationale | [API design (historical)](api-design.md) |
| 阅读中文概览 | [中文说明](zh-CN.md) |

## In one paragraph

Text is **not** parsed by the core. A `ProgramSource` turns an expression into a
`Program` (a pure-data record: `kind`, GLSL, `math`/`numpy` sources), and the model only
consumes that. `qmlmathplot.calc` is the bundled SymPy front end and is an optional extra;
a host may instead implement the `ProgramSource` interface or produce `Program`s in a
worker process and hand them over with `Plot.add_program()`. The core imports no SymPy and
owns no process, and `import qmlmathplot` loads neither Qt nor SymPy.

```python
from qmlmathplot import Plot
from qmlmathplot.calc import SymPyProgramSource

plot = Plot(program_source=SymPyProgramSource())   # [calc] extra
plot.add_curve("sin(1/x)")                         # the Program is ready synchronously
```

## Installation

```bash
pip install qmlmathplot                 # core: Qt + numpy, no SymPy
pip install "qmlmathplot[calc]"         # + SymPyProgramSource and the calc worker
pip install "qmlmathplot[matplotlib]"   # + Plot.to_matplotlib() / savefig(backend="matplotlib")
```

The core install is enough when the host produces `Program`s itself (for example from its
own worker process). `qmlmathplot[calc]` is needed for text input in-process and for the
`python -m qmlmathplot.calc` worker.

## Examples

- `examples/minimal.py` — standalone window / command line.
- `examples/explorer_qtquick.py` — Qt Quick component with input field, splitter and overlay.
- `examples/process_calculator.py` — calculator in a separate process, with `--check-isolation`.
- `examples/widget_minimal.py` — `MathPlotWidget` in a QtWidgets layout.
