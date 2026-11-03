# GGB oracle harness (temporary)

`GgbOracleTest.java` generates the semantic fixtures in
`tests/fixtures/ggb_oracle/` from the real GeoGebra interval path. It must be run
inside the GeoGebra source tree; never keep this file in that tree.

## Generate

```powershell
# 1. copy the harness into GGB (temporary)
copy tools\ggb_oracle\GgbOracleTest.java `
  D:\Projects\geogebra\source\shared\common-jre\src\test\java\org\geogebra\common\euclidian\plot\interval\

# 2. run it (from the GGB root)
cd D:\Projects\geogebra
.\gradlew.bat :shared:common-jre:test `
  --tests "org.geogebra.common.euclidian.plot.interval.GgbOracleTest"

# 3. remove it from the GGB tree
del source\shared\common-jre\src\test\java\org\geogebra\common\euclidian\plot\interval\GgbOracleTest.java
```

The harness writes one JSON file per case to
`D:/Projects/QMLMathPlot/tests/fixtures/ggb_oracle/`.

The baseline commit is `ae21d678407` (recorded in every fixture).

## What is captured

- `supported`: `IntervalFunctionSupport.isSupported` — the routing decision.
- `columns`: one per screen column, from `FunctionSampler` (the same sampler the
  real `IntervalPlotter` uses): the x interval and the `IntervalSet` topology
  (`EMPTY/CONNECTED/INVERTED/WHOLE/OVERFLOW`), with the payload bounds.
- `segments`: `IntervalPath` operations in screen coordinates, **without** the production
  plotter's `PLOT_MARGIN = 1` clip. This is the state-machine oracle; the plotter clip is a
  separate P2b layer.

The `EuclidianViewBounds` implementation uses the production mapping
(`toScreenCoordXd/Yd` are not identity), because the identity mock hides the
branch-reversal of `toScreenCoordYd`.

## Notes

- The harness is a temporary test, not vendored GGB code.
- `exp(tan(x))` and dense-pole cases are recorded, but are not exact-acceptance
  targets; GGB's own behavior is the reference.
- Do not regenerate fixtures with a different GGB commit without updating
  `ggb_commit`/the baseline and reviewing the diff.
