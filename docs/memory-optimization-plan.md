# Memory plan: corrected conclusion

Status: the per-activation leak was disproved (see below). Library fixes have since landed:
4× default MSAA, release of RHI resources on curve deletion/empty plots, byte-budgeted
renderer caches, and lazily created furniture Canvases. The remaining high-value work is
host-side lifecycle (keep one view alive, warm up, remove the extra layer), plus the
Symplify-only SVG cache policy.

## 1. What was retracted, and why

Two diagnoses were wrong, both because of the **measurement harness**, not the renderer:

1. "QML Canvas textures are not freed." A minimal 3-Canvas component showed only a one-time
   ~20 MiB growth; Canvas alone is not the leak.
2. "QML `Connections` to Python objects retain the Canvas." This looked true only because the
   harness toggled a QML `Loader` with `processEvents()` + `gc.collect()`. `processEvents()`
   does **not** process `DeferredDelete` events, so the unloaded `PlotView` (and its Canvas,
   RHI item, painters, and `Plot` default object) stayed alive. Each cycle added another
   old view: QQuickItem count grew 4 per cycle, `Plot` count 1 per cycle, and memory grew
   ~17–60 MiB per cycle. That was the harness accumulating views, not the component leaking.

**Correct harness**: run a real event loop (`QTimer`/`app.exec()`) while toggling the Loader,
or otherwise let Qt process deferred deletes. With that, live item counts return to the
baseline after unload (`QQuickItem` = 1 root, `CurveRhiItem` = 0), and memory plateaus.

## 2. Real event-loop measurements (Windows/D3D11, 900×700, GS60)

Metrics: `WorkingSetSize`, **`PagefileUsage` (commit)**, `PeakPagefileUsage`; live
`QQuickItem`/`CurveRhiItem` wrappers via `gc` + `shiboken6.isValid`. Toggling the Loader every
400 ms for 8–20 cycles.

| Scenario | Commit (MiB) | Result |
|---|---|---|
| 2 curves (`x**2`, `x**3/3`), GPU, 20 cycles | 276 → 350 by cycle 7, then **flat** (peak commit 369) | items after unload: 1 item, 0 RHI |
| 0 curves, GPU, 8 cycles | 214 → 250 by cycle 5, then flat | 1 item, 0 RHI |
| 0 curves, CPU, 8 cycles | 98 → 142 by cycle 4, then flat | 1 item, 0 RHI |
| 2 curves, GPU, 8 cycles, `processEvents`-only harness | 276 → 352, still growing | **invalid**: deferred deletes not processed |


### Host-pattern measurement (Symplify's PlotPane shape)

The host wraps the `PlotView` in `layer.enabled: true` + `OpacityMask` and lets RinUI
replace the page (and the view) on every navigation. Re-running the real event loop with
that shape, two curves:

| Pattern | Commit over 20 rebuilds | Peak commit |
|---|---|---|
| layer + `OpacityMask` around the Loader | 310 → 374 MiB (still stepping) | 393 MiB |
| plain Loader, no layer | 275 → 350 by cycle 7, then flat | 372 MiB |

So the layer adds roughly 20 MiB and some continued churn; the rest is the one-time RHI
warm-up. Both patterns are flat in **item counts** after unload.

### Attempted persistent view (reverted)

A `PlotHost` context object + one `PlotView` in `MainWindow`, reparented into the page's
placeholder, was implemented and smoke-tested: with a plot, the host and view were both
live at the same 524×836 geometry and the page rebuild did not create a second view.
However, the reparented view **crashed on shutdown** (0xC0000005) in the probe; the changes
were reverted, not shipped. A persistent view needs a proper page-destruction/reparent
lifecycle and shutdown test before it is safe. Do not resurrect the quick version.

The plateau is the one-time warm-up (QRhi/driver pipelines and buffers, QML compiles, Canvas
textures). It is not proportional to the number of page rebuilds after warm-up. The earlier
"17 MiB/activation" was the invalid harness's deferred-delete backlog.

## 3. What this means for the reported numbers

- **Steady +50–100 MiB** is most likely the one-time warm-up plateau (tens of MiB; a 20-cycle
  run added ~75 MiB commit before flattening), or the host retaining pages/`PlotView`
  instances. It is not a per-navigation leak in `qmlmathplot` under a real event loop.
- **Peak +500 MiB at plot instant** = one-time first plot activation (RHI item/device,
  pipeline creation, QML/text caches), plus the host's larger scene, a 4K pane, and
  `layer.enabled`/`OpacityMask` (extra full-pane texture and pass; measured +12 MiB at
  900×700, more at 4K/DPR). It is a transient, not necessarily a leak.
- The exact attribution must be made **in Symplify**, per process (UI vs calculator worker),
  with commit/peak commit and a real event loop. `WorkingSet` alone overstates GPU caches.

## 4. P0 — host-side plan (the actual fix)

1. **Keep one `PlotView` alive across page/navigation rebuilds.** The view model already owns
   the `Plot`; hoist the plot pane out of the rebuilt page (or cache the page). This avoids
   the warm-up plateau and the repeated RHI/pipeline transient at the plot instant.
2. **Warm up once at startup.** Create a minimal hidden `PlotView` (or activate the Loader
   once and keep it) so the first-plot RHI/pipeline cost is paid at startup. Steady state is
   unchanged; the plot-instant peak drops.
3. **Drop `layer.enabled` + `OpacityMask` around the plot** if rounded corners can be drawn
   otherwise (`clip` + background, or a pre-rendered mask). It adds a full-pane texture/pass.
4. **Verify the page really unloads**: after a rebuild, run the real event loop and assert
   `loader.item is None` and that the old `PlotView`s are gone (item count returns to the
   baseline). A page cache in the app will look exactly like this "leak".

These are Symplify changes; do not modify Symplify from this repository.

## 5. Library status

No library change is proposed. The experiments tried and reverted:

- replacing the Canvas layers with `QQuickPaintedItem` + weak Python connections;
- removing all QML `Connections` / replacing them with property bindings;
- making `CurveRhiItem`'s `CurveListModel` connections weak.

None changed the real-event-loop result (the plateau was unchanged); item counts were already
flat. Do not resurrect those rewrites without a harness that processes deferred deletes and
an app-side profile showing retained views.

If a future measurement *does* show retained `PlotView` instances in a host that has already
unloaded the Loader, the next step is to find the Python/QML ownership edge (not to rewrite
the renderer), using the item-count and `gc.get_referrers` method above.

## 6. Correct reproduction harness (real event loop)

```python
# after view.setSource(...) and exposure:
root = view.rootObject()
root.setProperty("backendPlot", plot)
root.setProperty("active", True)

class Driver(QObject):
    def __init__(self):
        super().__init__()
        self.i = 0
        self.on = True
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.tick)

    def tick(self):
        if self.on:
            root.setProperty("active", False)   # Loader unloads
            self.on = False
        else:
            print(self.i, mem())               # WorkingSet + PagefileUsage
            root.setProperty("active", True)
            self.on = True
            self.i += 1
            if self.i >= 20:
                app.quit()
                return
        self.timer.start(400)

d = Driver()
app.exec()
```

Use `PagefileUsage` (commit), not only `WorkingSetSize`. Do **not** use a bare
`processEvents()` loop as a memory-cycle harness: it leaves DeferredDelete events pending and
manufactures a linear leak.

## 7. Handoff prompt (paste into a new session)

```
You are working in D:/Projects/QMLMathPlot (QMLMathPlot 0.7.2). Read
docs/memory-optimization-plan.md; the earlier Canvas/Connections leak diagnosis was retracted.
The real event-loop measurements show no per-activation leak in PlotView: memory plateaus
after a one-time warm-up, and live QQuickItem/CurveRhiItem counts return to baseline after
the Loader unloads.

Do not rewrite the renderer, remove Canvas, change Connections, or add painters. The task is
host-side (Symplify, not this repo):
1. Stop destroying/re-creating the PlotView on page navigation: keep one instance alive, or
   hoist the plot pane out of the rebuilt page.
2. Warm up the plot once at startup with a hidden PlotView (or keep the first activation).
3. Remove layer.enabled/OpacityMask around the plot if possible.
4. In Symplify, measure with a real event loop (QTimer/app.exec), per process, using commit
   (PagefileUsage) and peak commit; verify after a page rebuild that loader.item is None and
   the old PlotView is gone. The reported +50–100 MiB steady is consistent with the warm-up
   plateau or retained pages, not with qmlmathplot leaking per navigation.

If you do find retained PlotView instances after a real unload, diagnose the Python/QML
ownership edge with item counts and gc.get_referrers; do not change the renderer.
```

## 8. Environment notes

- Python 3.14 venv via `uv`; PySide6 6.12; `uv run --no-sync` to avoid lock churn.
- Full suite ~107 s; `-p no:faulthandler` is required on this machine (AGENTS 43).
- Direct `github.com:443` is blocked; `origin` is
  `ssh://git@ssh.github.com:443/I-AM-A-NOOB/QMLMathPlot.git` (repo deploy key).
- `instructions.md` was deleted from HEAD in `51cdc57`; older history still has it.
