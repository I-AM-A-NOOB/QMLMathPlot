# Changelog

## 0.7.0 — unreleased

### Breaking

- **The core owns no process and no parser.** Text is resolved by an explicit, synchronous
  `ProgramSource` (`Plot(program_source=SymPyProgramSource())`); without one,
  `add_curve("...")` raises `ProgramSourceMissingError`. `add_program()` / `set_program()` and
  `add_points()` work without a source. Removed: `qmlmathplot.client`, `Plot.calculator`,
  `Curve.expr`, `Plot.to_sympy`.
- Construction resolves the `Program` synchronously; **the first qsb bake is asynchronous**
  (thread pool) as well as later ones. A failed bake keeps the previous shaders and reports
  the error (the new program is still the one the curve describes).
- QML `defaultExpression` was removed: a QML-created `Plot` has no text->Program source,
  so curves are always added by the host (which injects the model).
- QML `renderMode` is `"gpu"` (default) or `"cpu"`; the runtime `"auto"` value is gone. In cpu
  mode an implicit curve shows an explicit warning instead of a blank area.
- `Program.compile` uses an explicit `__builtins__ = {}`; programs cannot reach builtins.
- The matplotlib hand-off samples `Program.numpy`; the `matplotlib` extra no longer needs
  sympy. Implicit curves are still skipped there.

### Added

- `qmlmathplot.calc.SymPyProgramSource` (`[calc]`) as the synchronous SymPy front end.
- `Plot.add_program()` / `Curve.set_program()`; `Plot.plotError`; `Curve.program`.
- `ProgramSource` / `ProgramSourceMissingError` in `qmlmathplot.protocol`.


## 0.6.0 — 2026-11-10

### Breaking

- **The model no longer parses expressions.** A calculator turns text into a `Program`
  (`qmlmathplot.protocol`) and the model only consumes that; sympy is no longer imported by
  the UI process. The library ships `InlineCalculator` (default, in-process) and
  `ProcessCalculator` (`python -m qmlmathplot.calc`), and does not manage process lifecycle.
- `Curve` is asynchronous: `add_curve`/`expression` request a program and the shaders arrive
  later (`ready` state via `expressionChanged`/`shadersChanged`; a failure keeps the last
  drawing). `Curve.expr` is a deprecated, lazily imported sympy view.
- `Curve.expr` is no longer the internal representation; routing (function / interval path /
  level set) is decided by the calculator and exposed as `Program.kind`.
- `add_curve("a*x")` no longer raises: the third-symbol error is reported on the curve.
- Removed the Matplotlib-shaped front end: `Figure`, `Axes`, `FigureCanvasQt`, `pyplot`.
  `MathPlotWidget` stays.
- `pyproject`: sympy moved to the `[calc]` extra; `numpy` is a core dependency (programs may
  carry numpy sources); new `mpl` alias for the matplotlib hand-off.

### Added

- `qmlmathplot.protocol`: `Program` + framed JSON, the stable boundary for host implementations.
- `qmlmathplot.client`: `Calculator`, `InlineCalculator`, `ProcessCalculator` (framed JSON,
  timeouts, restart-after-crash, caller tokens).
- `qmlmathplot.calc`: text → `Program` (GLSL fragment/compute, `math` and `numpy` sources).
- `Curve.computeShader`, `Curve.mathSource`; `CurveListModel` gained a `mathSource` role for
  the CPU fallback.
- Examples: `process_calculator.py` (the process split, with `--check-isolation`) and
  `widget_minimal.py`; `explorer_qtwidgets.py` was removed.
- Tests: protocol, program construction, the worker round trip, and a subprocess test that
  proves the UI process never imports sympy.


## 0.5.0 — 2026-10-30

### Added

- **GeoGebra's interval path on the GPU.** `ggb_path.py` is the topology-in/segments-out
  specification of `IntervalPath`, `DrawInterval`, `DrawInvertedInterval` and `JoinLines`;
  `ggb_compute.py` mirrors it in a sequential compute shader. The compute path covers every
  expression whose operations GGB's `IntervalFunctionSupport` accepts (all except floor/sign/
  Min/Max/Piecewise and dependent quotients such as `x/sqrt(x**2)`).
- **Shader resources.** GLSL lives in `src/qmlmathplot/shaders/` (9 files + 5 common
  `@INCLUDE` blocks) and is assembled by `shaderbuild.py`, which rejects unresolved
  placeholders. The interval renderer is compute-only; the fragment is the sampled fallback
  (`oned_sampled.frag`).
- **Explicit CPU fallback** (`view.cpuRenderer`) drawing GGB's interval path as one
  `QPainterPath` per curve.
- **GPL-3.0-only**, with the GeoGebra-derived files attributed under EUPL-1.2
  (`THIRD_PARTY_NOTICES.md`, `LICENSES/EUPL-1.2.txt`).

### Changed

- Compute curves no longer bake the unused sampled fragment: `Curve.fragmentShader` and
  `vertexShader` are empty for them; the RHI builds the compute path from `expr`.
- The 1-D fragment is the sampled march only; the interval band lives in the compute path.
- The wrapped-pole (`exp(tan(x))`) rule is a Nyquist test on the expression's pole spacing;
  below it the branch ray is drawn, above it the attained half-plane is filled uniformly.
- `JoinLines` now matches GGB's tolerance and screen-space difference comparisons (fixes the
  wide-view `tan` breaks).
- Module layout: `model.py` is expression analysis/printing; `interval.py` the interval
  emitter; `shaderbuild.py` + `shaders/` the GLSL resources.

### Fixed

- `tan`/`1/x` asymptotes: no connector, correct branch width at large `|y|`.
- `exp(tan(x))` spike tops and the undersampled moiré.
- `sin(1/x)` envelope overfill; `log`'s deep descent width.
- Native test crash from a collection-time matplotlib import (lazy import in `test_handoff`).
