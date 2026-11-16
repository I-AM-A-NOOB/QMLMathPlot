# Memory optimisation plan (0.7.x)

Status: **investigated, fix design corrected, not implemented**. The working tree is clean;
HEAD is the 0.7.0 release plus the docs commit that added this file. This page records the
measurements, the corrected root cause, the fix options, and a ready-to-paste prompt for the
implementing agent.

> **Correction to the first version of this document.** The first pass said "QML Canvas
> textures are not freed". That is not the whole story: a minimal 3-Canvas component without
> `Connections` does **not** leak per activation. The leak is the combination of a Canvas
> layer and QML `Connections` to Python objects (the repaint handlers close over the Canvas;
> the Python signal connection keeps the closure/item alive). Removing **either** side
> removes the per-component leak, but only removing the `Connections` from the full
> production view makes repeated activations flat. Replacing Canvas with QQuickPaintedItem
> while keeping QML `Connections` is therefore **not** a fix. A Python-side painter with
> weak Python signal connections is.

## 1. Reported problem

In the downstream app (Symplify: one Qt Quick window, a lazily created `PlotView` inside a
`Loader`, two curves from `integrate(x**2, x)` — the original and the integral):

- peak total memory at the moment of plotting: **startup + ~500 MiB**;
- steady increase after plotting: **startup + 50–100 MiB**;
- other caches are negligible.

## 2. Measurements (Windows, D3D11, 900×700, working set / peak working set)

Method: `GetProcessMemoryInfo` (`WorkingSetSize`, `PeakWorkingSetSize`) in a Python process;
a QML `Loader` activates/deactivates the **production registered `PlotView`** (not a temp
copy) 6 times, pumping `processEvents` and `gc.collect()` between cycles. `off` = after
unload, `on` = after reload. The GPU baseline is the reliable reproduction; CPU mode does not
reproduce the steady leak (it reports ~1 MiB/cycle) and is **not** a valid acceptance check.

| Scenario | Result |
|---|---|
| Production `PlotView`, GPU, 6 cycles | **+233 MiB total** (251 → 484): e.g. +60, +38, +60, +1, +17, +56 per cycle |
| Production `PlotView`, GPU, **all `Connections` removed** | first activation **+120 MiB** (RHI one-time), then **+0.7 MiB/cycle** — flat |
| Production `PlotView`, GPU, each `Connections` with `Component.onDestruction: target = null` | **still leaks** (+61, +67, then +60 again) — do not use as the fix |
| Production `PlotView`, GPU, Canvas repaint via QML property bindings (no `Connections`) | +1, +120, +1, +65, +2, +60 — **not flat**; bindings are not sufficient |
| Production `PlotView`, CPU, 6 cycles | +0.8–1.3 MiB/cycle; the leak does not reproduce |
| `PlotFurniture` alone (2 Canvas + 4 `Connections`), GPU, 5 cycles | **+11.7 MiB/activation** |
| `PlotFurniture` with the Canvas blocks removed (Text + `Connections` left) | +0.7 MiB/cycle |
| `PlotFurniture` with the `Connections` removed (Canvas left) | +0.7 MiB/cycle |
| `PlotOverlay` alone (1 Canvas + 2 `Connections`), GPU, 5 cycles | **+11.9 MiB/activation** |
| `PlotOverlay` with the Canvas removed | ~+0.2 MiB/cycle after the first cycle |
| `PlotOverlay` with the `Connections` removed | +30, +12, then flat (noisy, but no steady leak) |
| 3 bare QML `Canvas` items (no `Connections`), 5 cycles | ~20 MiB **one-time**, then flat — Canvas alone is not the leak |
| `import sympy` + `integrate(x**2, x)` + build programs | peak **64.5 MiB** (import 53.3, integrate +11.2) |
| First `PlotCurves`/RHI activation | **+120 MiB one-time**, then flat across rebuilds |
| `layer.enabled` + `OpacityMask` around the plot | ~+12 MiB at 900×700; grows with pane size/DPR |

Interpretation:

- **SymPy integration is not the cause.** `x**2` peaks at ~65 MiB and the app already uses a
  worker process.
- **The steady growth is a Python-signal `Connections` retention problem around the Canvas
  layers.** `PlotFurniture` and `PlotOverlay` each own Canvas layers whose repaint handlers
  are attached with QML `Connections` to Python objects (`camera`, `plot`, `guides`). Each
  activation leaks about 12 MiB per component; the full view leaks 17–60 MiB per activation
  depending on timing/driver state. Removing all `Connections` makes the production view
  flat after the one-time RHI allocation; removing only the Canvas or only the Connections
  does it per component. A minimal Canvas without `Connections` does not leak.
- **The plot-instant peak includes a one-time RHI cost.** The first `PlotCurves` activation
  is +120 MiB (RHI item, shader/pipeline/driver resources) on top of the window/device. With
  a larger pane, `layer.enabled`, and the per-activation leak this is consistent with a
  several-hundred-MiB transient. Attribute the exact 500 MiB per process in the host (UI vs
  calculator worker) with `PeakWorkingSet64`.
- Working-set measurements on D3D11 are noisy: delayed driver frees and QML caches produce
  step jumps. Judge by the trend over ≥6 activation cycles, not one cycle. The production
  GPU baseline is reproducible; the temp-copy variants and CPU mode are not reliable for the
  full-view number.

## 3. Fix plan

### P0 — host-side (no library change, immediate)

1. **Keep one `PlotView` alive across page/navigation rebuilds.** The view model already
   owns the `Plot`; hoist the plot pane out of the rebuilt page (or cache the page). This
   removes the per-activation leak and the repeated RHI initialization regardless of the
   library fix.
2. **Warm up the plot once at startup.** Create a minimal hidden `PlotView` (or activate the
   `Loader` once and keep it) so the one-time +120 MiB RHI/pipeline cost is paid at startup,
   not at the first plot. Steady memory is unchanged.
3. **Drop `layer.enabled` + `OpacityMask` around the plot** if the rounded corners can be
   achieved otherwise. It adds a full-pane texture and a pass (+12 MiB measured at 900×700).

These are downstream (Symplify) changes; do not modify Symplify in this repository.

### P1 — library fix (root cause)

**Goal: no QML `Connections` to Python objects remain in the plot view, and the line layers
repaint without retaining their item.**

The 8 `Connections` to remove:

- `PlotFurniture.qml`: the grid Canvas (camera view, plot grid/style/tick changes) and the
  minor-tick Canvas (camera, minor ticks/colour/length/width).
- `PlotOverlay.qml`: the guide Canvas (camera, guides model).
- `PlotView.qml`: pan/zoom enabled and keyboard enabled (camera/input).

Recommended implementation:

- **Two `QQuickPaintedItem`s (Python)** replacing the three Canvas layers: one for the grid +
  minor ticks (in `PlotFurniture`), one for guides (in `PlotOverlay`). Keep the QML `Text`
  labels, title and the axis/tick `Rectangle` items unchanged. A single furniture painter is
  acceptable if it does not complicate the layering.
- The painter items expose a `plot` property (`QObject`); when it is set, they connect to
  `camera.viewChanged`, the relevant `Plot` style signals and the guides-model changes **in
  Python, with weak references** (`weakref.WeakMethod`) so the Python signal never owns the
  item. Disconnect on item destruction. Paint with `QPainter`, mirroring the existing QML
  code: `_deviceWidth` (integral device width), `_snapLine` (edges on device pixels), the
  dash patterns, and the grid/tick geometry.
- Do **not** drive the repaint through QML property bindings and do not declare
  `property var` arrays of Python properties; that was measured and is not flat. Do not keep
  a Canvas with a Python-side connection unless an experiment shows it is flat.
- Replace `PlotView`'s two input-enable `Connections` as well. They are not the dominant
  leak, but they keep closing over the whole view. Use QML `on...Changed` bindings (plain
  bool properties) or a small Python helper with weak connections; verify with the same
  cycle harness.
- If a Canvas is kept somewhere, the same rule applies: no QML `Connections` to a Python
  target, and no repaint request from a Python signal without weak ownership. Merging the
  three Canvases into one is **not** sufficient by itself (the retention is in the
  connection, not the texture count).

Constraints:

- Preserve the public API (QML `QmlMathPlot 2.0`, no new required properties).
- Preserve pixel behaviour: AGENTS 35/36 (hairline/axis snapping, axis pinning and labels),
  AGENTS 54 (offscreen export), and the existing render tests. The painter draws in device
  pixels; use the same rounding rules as `_deviceWidth`/`_snapLine`.
- Keep the QML `Text` labels; they do not leak measurably and their bindings are what keeps
  the furniture declarative.
- CPU mode must work without RHI (`QQuickPaintedItem` is fine); no Canvas dependency.
- Do not modify `D:/Projects/Symplify`. Do not change the public Python API.

### P2 — long-term (not this bug)

- Bound the renderer's per-expression pipeline/path-buffer caches (LRU); each distinct
  compute expression costs ~2.6 MiB of driver memory and the cache is currently unbounded.
- Store `add_points` data in `array('d')`/numpy instead of Python float tuples (100k points:
  11.5 MiB → ~1.6 MiB).
- CPU mode: one painter item for all curves instead of one full-size `QQuickPaintedItem` per
  curve (50 curves currently ~550 MiB in the software backend).

## 4. Acceptance criteria

- **Production `PlotView` GPU cycle test** (registered type, `sourceComponent`, 6
  activations, `processEvents` + `gc.collect`): after the first activation's one-time RHI
  cost, growth **≤ 2 MiB/activation** (current: tens of MiB/activation and +233 MiB over 6).
  The first activation may still add ~120 MiB; that is the RHI allocation, not a leak.
- Component-level checks: cycling `PlotFurniture` and `PlotOverlay` individually (or the new
  painter items) must be flat after the first activation.
- CPU mode stays at ~1 MiB/cycle (do not use CPU alone as the proof).
- Existing render tests stay green: `test_render_smoke.py` (grid/ticks/axes/title, axis
  thickness, axis pinning), `test_implicit.py`, `test_cpu_backend.py`, `test_export.py`,
  `test_qtquick_coexistence.py`.
- `ruff check src tests examples` clean; full suite baseline 277 passed / 3 xfailed
  (`uv run pytest -p no:faulthandler`, ~107 s).
- No public API changes; the QML URI stays `QmlMathPlot 2.0`.
- Furniture repaint cost does not regress meaningfully (repaint on camera/view changes, not
  per frame).

## 5. Reproduction harness (for the implementing agent)

The exploratory scripts were deleted; recreate this minimal production harness.

QML (`.tmp_cycle_prod.qml`):

```qml
import QtQuick
import QmlMathPlot 2.0
Item {
    id: root
    property bool active: false
    property QtObject backendPlot
    Component { id: comp; PlotView { } }
    Loader {
        anchors.fill: parent
        active: root.active
        sourceComponent: comp
        onLoaded: item.plot = root.backendPlot
    }
}
```

Python: create `QGuiApplication`, `register_qml_types()`, `Plot(program_source=None)`,
`plot.renderMode = "gpu"` (or `cpu`), a `QQuickView` at 900×700, set the source, expose
`plot` as `backendPlot`, show, wait for `isExposed()` by pumping events. Then 6 times:
`root.setProperty("active", False)`, 20 `processEvents`, `gc.collect()`, read RSS;
`root.setProperty("active", True)`, 60 `processEvents`, `gc.collect()`, read RSS. Print
`WorkingSetSize`/`PeakWorkingSetSize` from `psapi.GetProcessMemoryInfo`.

Expected current production GPU numbers (baseline): 251 → 312 → 350 → 410 → 411 → 428 →
484. With **all `Connections` removed** from `PlotFurniture.qml`, `PlotOverlay.qml` and
`PlotView.qml`: 251 → 371 (first RHI) → +0.7/cycle. The removed-Connections run does not
repaint correctly; it is a diagnostic, not the fix.

## 6. Handoff prompt (paste into a new session)

```
You are working in D:/Projects/QMLMathPlot (QMLMathPlot 0.7.0 + one docs commit). Read
docs/memory-optimization-plan.md first; it contains the measured diagnosis and the corrected
fix design. The working tree is clean.

Task: implement the library-side fix P1 — remove the per-activation leak caused by QML
Connections to Python objects around the Canvas line layers, and replace those line layers
with QQuickPaintedItem-based painters connected on the Python side with weak references.

Reproduce first (production PlotView, GPU, 6 activation cycles; harness in the plan):
- baseline: ~+60 MiB/activation (e.g. 251 → 312 → 350 → 410 → 411 → 428 → 484);
- after removing all Connections from PlotFurniture.qml, PlotOverlay.qml, PlotView.qml:
  first activation +120 MiB (RHI), then flat (+0.7 MiB/cycle);
- `Component.onDestruction: target = null` does NOT fix it;
- replacing Connections with QML property bindings does NOT fix it.
Do not repeat those failed experiments.

Requirements:
- Replace the grid + minor-tick Canvas (PlotFurniture.qml) and the guide Canvas
  (PlotOverlay.qml) with QQuickPaintedItem(s) in Python. Keep the QML Text labels, title and
  axis/tick Rectangle items. The painter exposes a `plot` property, connects to
  camera/plot/guides signals in Python with weak references, and disconnects on destruction.
  Mirror `_deviceWidth`/`_snapLine`/dash patterns exactly (device-pixel snapping).
- Remove the QML `Connections` from those files and from PlotView.qml; implement the
  pan/zoom/keyboard-enable behaviour without QML Connections (plain QML property bindings or
  a Python helper with weak connections).
- Preserve QML API (QmlMathPlot 2.0), no required new properties; do not modify
  D:/Projects/Symplify; do not change the public Python API.
- Preserve pixels and export: AGENTS items 35/36 (hairline/axis snapping, pinning),
  AGENTS 54 (offscreen export), test_render_smoke.py, test_export.py,
  test_qtquick_coexistence.py, test_cpu_backend.py, test_implicit.py.
- Add a subprocess/registered-PlotView cycle regression test on GPU asserting bounded
  growth after the first activation (mark gui/slow; keep it out of the non-GUI CI path).
  Use 6 cycles and the plan's harness; CPU mode is not the acceptance check.
- Run: `uv run ruff check src tests examples`; `uv run pytest -p no:faulthandler` (full,
  ~107 s). Baseline: 277 passed, 3 xfailed.
- Report: before/after cycle tables, files changed, tests run, and any pixel/behaviour
  differences.

Out of scope: P0 host changes (keep one PlotView alive, warm-up, remove layer) and P2 items
(LRU caches, array('d') data, single CPU painter item).
```

## 7. Environment notes for the next session

- Python 3.14 venv via `uv`; PySide6 6.12; `uv run --no-sync` to avoid lock churn.
- Full suite ~107 s; `-p no:faulthandler` is required on this machine (AGENTS 43).
- Direct `github.com:443` is blocked here; `origin` is
  `ssh://git@ssh.github.com:443/I-AM-A-NOOB/QMLMathPlot.git` (repo deploy key). Do not
  tag/release unless asked.
- `instructions.md` was deleted from HEAD in `51cdc57`; older history still has it.
