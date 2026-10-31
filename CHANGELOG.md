# Changelog

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
