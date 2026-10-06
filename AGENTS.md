# AGENTS.md — pitfalls already hit (do not re-learn them)

Working notes for agents and contributors. Each item is **symptom → cause → fix**, with
the measurement that settled it. Keep code comments short (design rationale only); long
histories and numbers belong here.

## Shader generation

1. **Macro arguments must be parenthesized.** The expression is inlined into
   `#define F(u) (...)`; macros are *textual*. `F(x - h)` with `SIN(1.0/u)` expands to
   `SIN(1.0/x - h)`, so all sample points evaluated `1.0/x - h_k` (differing by a tiny
   offset): samples looked identical, `turns = 0`, `spread ≈ 0`, band weight ≈ 0 — **the
   envelope band never triggered at all**. `func_glsl(..., wrap=True)` prints the argument
   as `(u)`; `test_model.py` has a regression.
2. **GPU `sin`/`cos` are unreliable for large arguments.** Near the `sin(1/x)` singularity
   the argument reaches hundreds or thousands. Measured on Intel D3D11 (same on OpenGL):
   `cos(1/x)` returned near-zero garbage and the sample points came out nearly identical.
   Generated expressions wrap `sin`/`cos` into `SIN`/`COS`, which reduce the argument into
   `[0, 2π)` first (small arguments are accurate everywhere). `d1` went from garbage ≈ 200
   back to the correct ≈ −18200.
3. **qsb decides the stage from the source file suffix.** Naming the source `.glsl` makes
   qsb treat it as *vertex*, so the fragment shader is baked as a vertex shader → D3D11's
   pixel stage receives invalid HLSL (`X4502`) and the Intel driver hangs on the first
   frame. The GL backend hides this because it assigns stages by slot. Always write
   `.vert` / `.frag` (see `qsb.bake()` and `test_shader_bake.py`).
4. **Expressions are macros, not GLSL user functions.** A pixel evaluates the same `x`
   25 times (1 stroke sample + 8 narrow + 16 wide); macros are preprocessor expansion with
   no call semantics, which is what we want. Do not "clean this up" into functions.
   (An earlier note blamed user functions for an Intel D3D11 hang — that was a
   misdiagnosis: the real cause was item 3.)

## Stroke (implicit signed-distance drawing)

5. **Tangent-based distance saturates on steep columns.** With huge `|f'|`, the tangent
   approximation reports a tiny horizontal distance for *any* y, so `cov` saturates to the
   whole column and extrapolates far outside the true range (measured: `sin(1/x)` painted
   up to ±1.8 although its range is ±1). Fix: measure the distance to the **sampled
   polyline segments** (Desmos' approach), clamp the stroke with the sampled envelope, then
   replace triggered columns with the band.
6. **Per-column sampling is phase noise for oscillating functions.** When a column's
   samples happen to miss the extrema or bunch together, the criteria misjudge
   ("resolvable") or the envelope comes out too narrow — visible as comb-like alternating
   solid/empty columns and steps along the band edge (measured: neighbouring columns'
   top edge differed by **93.6 px on average, 421 px max**). Fix: two rulers —
   **narrow window (±2 columns, 8 points)** decides *which* columns cannot be drawn
   (narrow ⇒ the fill cannot be widened), **wide window (±8 columns, 16 points)** only
   estimates the band's top/bottom edges (more samples ⇒ more likely to hit extrema).
   Measured afterwards: top-edge difference between neighbouring columns **2.4 px**, band
   width 20 logical columns (matches a numpy reference implementation's 20/900).
   Deliberate trade-off: **rather overfill by one ring of columns than draw comb artifacts.**
   Post-fix verification (1350×900, default view, D3D11 and OpenGL pixel-identical): the
   `sin(1/x)` singularity column lights 0.50 of its height (= half a column of the ±1
   envelope) with flat edges, and no solid pixel outside |y| > 1 within 41 centre columns
   (7961 such pixels before the fix).

## Band criteria (undersampled columns)

7. **The old criterion (b) misfired and was deleted.** "Measured spread ≪ derivative
   prediction" with a *soft* threshold `smoothstep(0.15, 0.5, …)` wrongly filled monotone
   steep curves (`log(x)` ratio ≈ 0.28 → whole column filled, curve appeared to vanish) and
   resolvable extrema (a `sin(x)` peak became a block). Replaced by hard criterion (d):
   `pred ≥ 16 · spread` **and** `pred ≥ 4 px`. Thresholds measured: monotone concave `log`
   ≈ 3.6, `tan`/`1/x` branches < 1, genuinely sub-pixel `sin(1/x)` ≈ 58.
8. **Pixel-unit gates break under deep zoom.** `step(spread, 4 * size.y)` was meant to stop
   poles from flooding a column, but it is in *pixels*: once the viewport spans < 0.5 world
   units, `sin(1/x)`'s ±1 range maps to more than 4 viewport heights, the gate switches the
   band off entirely → **full-screen vertical stripes** (measured: zoom step 120 rendered
   completely blank). Poles are handled by the analytic gap (world units, denominator sign
   change) instead.
9. **The "jump" break cut legitimate steep segments.** `|Δ| > 4 · viewport height` is a
   pixel/viewport-relative quantity; under magnification it also cut legitimate steep
   segments, showing up as *fragmented* vertical stripes. Removed; the analytic pole gap
   alone keeps `1/x` and `tan(x)` from connecting across asymptotes (covered by
   `test_asymptote_is_not_connected`).
10. **The apex bisection was removed.** Refining extrema of reversing segments gained only
    ~1 device pixel of peak height, while it inlined the derivative expression 3× inside the
    unrolled sampling loop and noticeably slowed shader compilation (first frame). The
    shader source went 8.1 KB → 6.9 KB when it was dropped.
11. **8-point turn counting misses ~11 % of columns.** With the narrow window at 8 points,
    random sampling made ~11 % of columns report `turns < 2` → residual vertical stripes.
    Sampling 16 points globally costs 8 extra `f` evaluations per pixel for every pixel.
    Fix: 8 points normally, and inside the band region only, 8 additional interleaved points
    (the narrow loop samples the *even* positions of the 16-point grid) are merged in for
    the turn count. Measured: empty columns at zoom step 100 went 0.89 % → 0.15 % (= the
    analytic pole gap), with no change to the worst-case frame cost.

## Domain, poles, unbounded edges

12. **Pole gaps are analytic, not pixel-guessed.** `POLE(u)` (denominator sign change, world
    units) plus "value far beyond the viewport" leaves a gap exactly at the pole, so `1/x`
    and `tan(x)` do not draw vertical connectors, while their branches still reach the
    asymptote.
13. **`log(x)`'s descent vanishes in deep views.** `log` diverges *slowly* (logarithmically),
    so a fixed-width sampling window never reaches deep enough values: in a deep viewport the
    visible curve sits at `x ≈ e^y` (e.g. `y ∈ [−16, −12]` ⇒ `x ∈ [1e-7, 6e-6]`), while the
    leftmost sample only reaches `log ≈ −7` → the whole segment disappears. Fix:
    `unbounded_edges()` (sympy one-sided limits) extends the polyline along the boundary `x`
    as a vertical ray to ±1e12. The ray lies exactly on the boundary x, so line width stays
    constant and columns stay aligned; the `x < 0` half is cut by the domain.
    Fast-diverging functions (`1/x`, `tan`) need no ray (samples already leave any viewport),
    and `sqrt` (limit 0) / `sin(1/x)` (no limit) are not listed.
14. **`unbounded_edges()` must only analyse polynomial boundaries.** `tan(x)`'s condition
    `cos(x) = 0` costs **94 ms** through sympy `solve` + `limit` (and is useless: tan diverges
    fast, sampling reaches deep enough anyway). Restricting to `lhs.is_polynomial(sym)`
    brought it to **0.9 ms**.

## Qt / QML / packaging

15. **Read pixels with the async `QQuickItem.grabToImage()`**, never
    `QQuickWindow.grabWindow()`: the latter's synchronous handshake deadlocks against
    Python-side scene-graph objects (the window shows as "not responding").
16. **`QSG_RHI_BACKEND` must be set before `QGuiApplication`.** Qt reads it during platform
    initialisation; calling `QQuickWindow.setGraphicsApi()` afterwards has no effect (the
    surface is already created for the default backend — observed as
    `QRhiGles2: Failed to make context current`).
17. **One `QCoreApplication` per process.** The test session fixture therefore creates a
    `QApplication` (a subclass of `QGuiApplication`, so the QML tests are unaffected) — see
    `tests/conftest.py`.
18. **`QWidget.scroll` name collision.** Assigning `self.scroll = QScrollArea()` shadows the
    inherited `QWidget.scroll(dx, dy)` method. Name scroll areas `scroll_area`.
19. **Qt `Property` descriptors need a value-type annotation** (`expression: str = Property(...)`).
    Without it type checkers only see the `Property` object and flag every Python-side read
    and write. See `viewmodel.PlotController`.
20. **`MathPlotWidget` is exported lazily** (module-level `__getattr__` in
    `qmlmathplot/__init__.py`, with a `TYPE_CHECKING` import so type checkers still see the
    class). Importing the package for pure-QML use must not pull in `QtWidgets`.
21. **`QQuickWidget` renders into its own FBO**, so the plot coexists with sibling widgets
    (input fields, `QScrollArea`, `QSplitter`, overlays) — a raw `QQuickWindow` does not.
    The QML side accepts wheel/drag events, so a parent scroll area never receives them
    (verified in `tests/test_widget.py`).

22. **The QML component must be registered as a type, not just loaded as a file.**
    Loading `qml/MathPlot.qml` by URL (the `QQuickView` path) works without registration,
    so `import QmlMathPlot 1.0` + `MathPlot { … }` silently failed with
    *"MathPlot is not a type"* until `register_qml_types()` also called
    `qmlRegisterType(QUrl.fromLocalFile(qml_component_path()), …)`. Registration is
    guarded by a module-level flag because Qt complains about duplicate registrations
    (the widget registers on every construction otherwise).

## Performance (measured)

Startup to first frame is ≈ **230 ms** (Windows / D3D11 / 900×600):

| Stage | Cost | Note |
|---|---|---|
| Qt graphics device init (RHI/D3D11) | **~160 ms** | The first window's `show → exposed`; a **bare window without any ShaderEffect is just as slow**, so this is not the shader's fault |
| Window + QML load + first frame | ~80 ms | Measured from the second window on (device already created) |
| `qsb` bake | 0 / ~126 ms | Cached by source hash: 0.15 ms on a hit; a source change re-bakes once |
| Expression analysis (sympy) | 0.6–3 ms | See item 14 |

Per-frame GPU cost (2400×1500, extreme zoom, worst case) **31 ms**; at 900×600 it is about
1/7 of that, well below 60 Hz's 16.7 ms. Fragment shader source ≈ 6.9 KB.
