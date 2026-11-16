# Memory optimisation plan (0.7.x)

Status: **investigated, not implemented**. The working tree is clean at 0.7.0. This page
records the measurements, the two identified causes, the planned fixes, and a ready-to-paste
prompt for the implementing agent.

## 1. Reported problem

In the downstream app (Symplify: one Qt Quick window, a lazily created `PlotView` inside a
`Loader`, two curves from `integrate(x**2, x)` — the original and the integral):

- peak total memory at the moment of plotting: **startup + ~500 MiB**;
- steady increase after plotting: **startup + 50–100 MiB**;
- other caches are negligible.

## 2. Measurements (Windows, D3D11, 900×700, working set / peak working set)

Method: a Python process with `GetProcessMemoryInfo` (`WorkingSetSize`,
`PeakWorkingSetSize`); a QML `Loader` activates and deactivates a `PlotView` (or one of its
child components) repeatedly, with `processEvents` and `gc.collect()` between cycles.

| Scenario | Result |
|---|---|
| `import sympy` + `integrate(x**2, x)` + `build_program` for source and result | peak **64.5 MiB** (import 53.3, integrate +11.2) |
| Empty `PlotView` shell (background only), 5 cycles | flat |
| `PlotCurves` (RHI item), first activation | **+120 MiB one-time**; later cycles flat |
| `PlotFurniture`, 5 cycles | **+11.7 MiB per activation**, retained after unload |
| `PlotOverlay`, 5 cycles | **+11.9 MiB per activation**, retained after unload |
| `PlotLegend`, `PlotInput`, 5 cycles | flat |
| Full `PlotView`, 5 cycles | **+17 MiB per activation** (288 → 305 → 322 → 339 → 356) |
| Full `PlotView`, first activation | **+172 MiB** (116 → 288) |
| CPU mode (`renderMode="cpu"`, software scene graph), `PlotView` cycles | same +17 MiB/cycle |
| 3 bare QML `Canvas` items, 5 cycles | ~17–20 MiB growth; `Canvas.Immediate` / `FramebufferObject` do not fix it |
| `layer.enabled` + `OpacityMask` around the plot | ~+12 MiB (at 900×700; grows with pane size/DPR) |

Interpretation:

- **SymPy integration is not the cause.** `x**2` peaks at ~65 MiB, and the app already runs
  integration in a worker process.
- **The steady +50–100 MiB is the QML `Canvas` leak.** `PlotFurniture` owns two `Canvas`
  items (grid, minor ticks) and `PlotOverlay` one (guides). Each full-size Canvas texture
  (~5.7 MiB at 900×700/DPR 1.5) is **not released when the item is destroyed**. They add up
  to about 17 MiB per `PlotView` activation; 3–6 page/navigation rebuilds give 50–100 MiB.
  `PlotCurves`/RHI is flat across rebuilds (Qt reuses the pipelines within the window).
- **The plot-instant peak includes a one-time RHI cost.** The first `PlotCurves` activation
  is +120 MiB (RHI item, shader/pipeline/driver resources) on top of the window/device;
  with a larger pane, `layer.enabled`, and the Canvas textures this is consistent with a
  several-hundred-MiB transient. The exact 500 MiB must be attributed per process in the
  host (UI vs calculator worker) with `PeakWorkingSet64`.

Measurement caveats: working set includes D3D11 driver allocations and QML caches; the
numbers vary with GPU, DPR and pane size. The leak is in Qt's `Canvas` texture lifecycle (a
minimal 3-Canvas component reproduces it), triggered by our three Canvas layers.

## 3. Fix plan

### P0 — host-side (no library change, immediate)

1. **Keep one `PlotView` alive across page/navigation rebuilds.** The view model already
   owns the `Plot`; hoist the plot pane out of the rebuilt page (or cache the page). This
   removes the 17 MiB/activation leak and avoids re-creating the RHI item.
2. **Warm up the plot once at startup.** Create a minimal hidden `PlotView` (or activate the
   `Loader` once and keep it) so the one-time +120 MiB RHI/pipeline cost is paid at startup,
   not at the first plot. Steady memory is unchanged.
3. **Drop `layer.enabled` + `OpacityMask` around the plot** if the rounded corners can be
   achieved otherwise (clip + background, or a pre-rendered mask). It adds a full-pane
   texture and a pass; measured +12 MiB at 900×700.

These are downstream (Symplify) changes; do not modify Symplify in this repository.

### P1 — library fix (root cause)

Replace the three QML `Canvas` layers with something whose textures are released:

- Option A (preferred): **one `QQuickPaintedItem`** (Python) per layer — or a single
  furniture painter — drawing grid, minor ticks, axis lines/ticks and guides. Keep the QML
  `Text` labels as they are. The painter reads `plot.camera` and the style properties and
  repaints on their notify signals. `QQuickPaintedItem` textures are freed on destruction
  (verified in CPU mode: two `PathCurveItem`s released ~21 MiB on unload).
- Option B: evaluate `QtQuick.Shapes` (`Shape`/`ShapePath`) for the line layers: scene-graph
  geometry, no raster texture. Check dashed grid lines and antialiasing; if `Shape` cannot
  express the styles, fall back to A.
- Option C (stopgap only): merge the three Canvases into one. It reduces the leak from
  ~17 MiB to ~5.7 MiB per activation but does not eliminate it.

Constraints:

- Preserve the public API (QML `QmlMathPlot 2.0`, no new required properties).
- Keep the pixel snapping rules (AGENTS 35/36: grid hairlines and the 1.6-device-px axis
  must stay stable across pans), the axis pinning/clamping (AGENTS 36), and the offscreen
  export's Canvas paint drain (AGENTS 54 — if Canvas is removed, this may simplify).
- Keep the text labels (tick labels, title, axis labels) as QML `Text`; only the line layers
  move to a painter/shape.
- CPU mode must work without RHI; the painter items are plain `QQuickPaintedItem`.

### P2 — long-term (not this bug)

- Bound the renderer's per-expression pipeline/path-buffer caches (LRU); each distinct
  compute expression costs ~2.6 MiB of driver memory and the cache is currently unbounded.
- Store `add_points` data in `array('d')`/numpy instead of Python float tuples
  (100k points: 11.5 MiB → ~1.6 MiB).
- CPU mode: one painter item for all curves instead of one full-size `QQuickPaintedItem`
  per curve (50 curves currently ~550 MiB in the software backend).

## 4. Acceptance criteria

- A cycle test (subprocess) that creates/destroys a full `PlotView` 5 times reports **bounded**
  RSS growth (target: < 20 MiB total after warm-up; current: ~85 MiB). Mark it `gui` / slow;
  do not run it in the non-GUI CI job.
- Existing render tests stay green: `test_render_smoke.py` (grid/ticks/axes/title, axis
  thickness, axis pinning), `test_implicit.py`, `test_cpu_backend.py`, `test_export.py`
  (offscreen Canvas drain), `test_qtquick_coexistence.py`.
- `ruff check src tests examples` clean; full suite baseline 277 passed / 3 xfailed
  (`uv run pytest -p no:faulthandler`).
- No public API changes; the QML URI stays `QmlMathPlot 2.0`.
- Furniture repaint cost does not regress meaningfully (furniture is repainted on
  camera/view changes, not per frame).

## 5. Reproduction harness (for the implementing agent)

The exploratory scripts were deleted; recreate a minimal one:

1. Build two `Program`s in a separate process (`x**2`, `x**3/3`) and write them as JSON
   (`build_program(...).to_json()`).
2. A QML root with a `Loader` whose `sourceComponent` is a full `PlotView` (or a variant with
   individual children). Toggle `active` 5 times, pumping `processEvents` and `gc.collect()`
   between; print `WorkingSetSize`/`PeakWorkingSetSize` via `psapi.GetProcessMemoryInfo`.
3. Baseline (before any activation) vs after each cycle. The current result is +17 MiB/cycle
   for the full component, +11.7 for `PlotFurniture`, +11.9 for `PlotOverlay`, flat for
   `PlotCurves` after its one-time +120 MiB.

## 6. Handoff prompt (paste into a new session)

```
You are working in D:/Projects/QMLMathPlot (QMLMathPlot 0.7.0, released). Read
docs/memory-optimization-plan.md first; it contains the measured memory diagnosis and the
fix plan. Do not re-investigate the measurements from scratch unless something contradicts
them; re-run the cycle harness only to compare before/after.

Task: implement the library-side fix P1 — remove the per-activation QML Canvas texture leak
from PlotFurniture.qml (grid canvas + minor-tick canvas) and PlotOverlay.qml (guide canvas).

Requirements:
- Preferred: replace the three Canvas layers with QQuickPaintedItem-based painter(s) in
  Python (one item drawing grid/minor/axis/ticks/guides is acceptable), keeping the QML Text
  labels unchanged. Evaluate QtQuick.Shapes as an alternative first if it can express the
  dashed grid and the current snapping; otherwise use QQuickPaintedItem.
- Preserve QML API (QmlMathPlot 2.0), no required new properties.
- Preserve pixels: AGENTS items 35/36 (hairline/axis snapping, axis pinning and labels),
  AGENTS 54 (offscreen export / Canvas drain), and the existing render tests
  (test_render_smoke.py, test_export.py, test_qtquick_coexistence.py, test_cpu_backend.py).
- CPU mode must keep working without RHI (QQuickPaintedItem is fine); no Canvas-dependent
  behaviour.
- Do not modify D:/Projects/Symplify. Do not change the public Python API.
- Keep the three-layer separation only if it does not reintroduce the leak; a single
  furniture painter is acceptable.
- Add a subprocess cycle regression test (5 create/destroy cycles of a PlotView) asserting
  bounded RSS growth after warm-up; mark it gui/slow and keep it out of the non-GUI CI path.
- Run: `uv run ruff check src tests examples`; `uv run pytest -p no:faulthandler` (full,
  ~2 min; GUI tests are slow, do not kill early). Baseline: 277 passed, 3 xfailed.
- Report: before/after cycle numbers, files changed, tests run, and any pixel/behaviour
  differences.

Out of scope: the P0 host changes (keep one PlotView alive, warm-up, remove layer) and the
P2 items (LRU caches, array('d') data, single CPU painter item).
```

## 7. Environment notes for the next session

- Python 3.14 venv via `uv`; PySide6 6.12; `uv run --no-sync` to avoid lock churn.
- GUI tests take ~107 s; `-p no:faulthandler` is required on this machine.
- `github.com:443` is blocked; the `origin` remote is `ssh://git@ssh.github.com:443/...`
  with a repo deploy key. Do not tag/release unless asked.
- `instructions.md` was removed from HEAD (it still exists in older history).
