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

12. **Pole connectors come from three places; each needs its own guard.** `tan(x)` and `1/x`
    must not draw a line across a pole, and the old single guard — the "value far beyond the
    viewport" gap, `(nh - nl) > 32 × viewport` — is not enough once the `log(x)` ray exists
    (item 13): measured, `tan(x)` drew **5 full-height columns** (900 of 900 px) at x ≈ ±1.55
    and ±4.7, and `1/x` two at x ≈ ±0.02. Three separate causes, three fixes, all decided in
    world units (pole-denominator sign changes) so they do not drift with zoom:
    * the **stroke** joined the two samples that straddle the pole → a polyline segment whose
      endpoints' pole-denominator product is ≤ 0 is skipped (`POLE(prev) * POLE(next) > 0`).
    * the **band** filled the pole column: the 16-point turn count read the jump as a turn —
      measured `turns16 = 2`, i.e. the jump itself *and* the following pair, whose difference
      has the opposite sign because the jump also seeded the comparison. Excluding straddling
      pairs from the count *and* clearing the previous difference takes it to 0 → `w = 0`.
    * the **boundary ray** (item 13) existed at all: `unbounded_edges()` analysed `1/x`'s
      polynomial boundary x = 0 and emitted one, and the shader drew a pale column at
      x = ±0.014 (the query window ±h hit the boundary from a one-sided column, and the ray
      then ran from that column's positive extreme through the viewport). Fixed at the source:
      a boundary where a *pole* factor vanishes gets no edge at all — measured,
      `unbounded_edges(1/x)` went from `[(0, down, up)]` to `[]`. The shader then needs none of
      the guards that had been clipping the ray's own stroke.
    Measured after: the tallest column near a pole is **28 px** (tan) / **23 px** (1/x) of 900,
    the branches still reach the asymptote, and `sin(1/x)`'s band is untouched (24 columns at
    50 %, the ±1 envelope). The 32 × viewport gap is kept as a second line of defence, but it
    is not what removes the connectors — at tan's pole column the spread was 115 world units
    against the 128 threshold, so it never fired.
13. **`log(x)`'s descent vanishes in deep views.** `log` diverges *slowly* (logarithmically),
    so a fixed-width sampling window never reaches deep enough values: in a deep viewport the
    visible curve sits at `x ≈ e^y` (e.g. `y ∈ [−16, −12]` ⇒ `x ∈ [1e-7, 6e-6]`), while the
    leftmost sample only reaches `log ≈ −7` → the whole segment disappears. Fix:
    `unbounded_edges()` (sympy one-sided limits) extends the polyline along the boundary `x`
    as a vertical ray to ±1e12. The ray lies exactly on the boundary x, so line width stays
    constant and columns stay aligned. (The `x < 0` half is *not* cut by the domain: the stroke
    is a band centred on the boundary, so the ray is painted there too — see the width note
    below.)
    Fast-diverging functions (`1/x`, `tan`) get no ray at all: a boundary that is also a pole is
    dropped by `unbounded_edges()` (item 12). The ray is drawn only when the samples have not
    already reached that viewport edge — its reason to exist — starting at the column's own
    extreme, or at the viewport edge when the column has no valid sample: a pixel just *outside*
    the domain is still inside the stroke's band, which is centred on the boundary.
    That band is where the width lives, and it used to be clipped twice. `_ray_dist`'s query
    window is the sampled interval **widened by the stroke radius** (`lineWidth/2 + 0.5`),
    because the coverage ramp reaches that far; and the domain test at the top of `main` now
    lets a ray through instead of returning transparent, which used to remove the entire outer
    half of the descent's band. A column with no valid sample at all (a pixel just outside the
    domain) must not be judged by that sentinel, though: its extent is probed just *inside* the
    domain (`F(ex_ ± 0.25·dx)`), or `log(x-1)` grows a full-height line at its own boundary in a
    shallow view (measured: two columns of 900 px at x = 1, where the visible curve is nowhere
    near it). Measured on the ray-drawn part of the descent (1350×900 frame, y ∈ [−8, −4], rows
    from the middle down) the width went **1.00-1.67 → 3.00-3.23** device px against a nominal
    3.00; the rows above it are drawn by the polyline alone and keep the steep-slope residual of
    the ±1-column window (item 24). Regression:
    `test_render_smoke.py::test_log_descent_is_not_cut_and_keeps_its_width` and
    `::test_a_shifted_log_does_not_draw_a_line_at_its_boundary`. The `y ∈ [−16, −12]` column is
    still lit 100 %, at full width (3.00 on every sampled row).
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

23. **A QML `Flickable` steals the drag from the plot.** Inside a `ScrollView` the plot's
    panning stopped after ~18 px — the flickable's drag threshold — because
    `QQuickFlickable` takes over the mouse grab. Fix: `preventStealing: true` on the plot's
    MouseArea. Verified: a 300 px drag now pans 300 px worth of plot coordinates, 1:1 with
    the cursor (measured `xmin` moved 3.0 units = 300/1200 × 12 world units), while the
    flickable's `contentX` stays 0. Regression test: `tests/test_qtquick_coexistence.py`.

24. **The stroke's sampling window must be wide enough for the perpendicular foot.** With
    the narrow window at ±0.5 columns the polyline segments were only 0.25 columns long. On a
    sloped line a pixel's perpendicular foot is displaced along x by `m·d/√(1+m²)` (up to
    ~1 px at the coverage ramp's edge), so the foot fell outside the segment and `_seg_dist`
    returned the distance to the nearest *endpoint* instead: the coverage collapsed and the
    stroke looked thin and ragged on steep parts. Effective width (device px, nominal 2.25 at
    DPR 1.5) measured with straight lines `k·x`, area ÷ length:

    | screen slope | before | after (±1 column) |
    |---|---|---|
    | 2 (63°) | 1.92 | **2.24** |
    | 10 (84°) | 1.43 | **2.10** |
    | 40 (89°) | 1.31 | **2.05** |
    | 200 (89.7°) | 1.30 | **2.02** |

    ±1 column fixes it with no extra `f` evaluations, and it also *widens* the band: at the
    default view of `sin(1/x)` the solid band went from 7 to 14 columns of 900 (central 40
    columns: 18 % → 35 % filled), because the wider window sees more direction turns. The
    "20 columns" figure in item 6 predates several revisions of the band criteria and is not
    directly comparable. ±2 columns reaches the nominal width
    everywhere but over-triggers the band criteria (calibrated for this window): a render
    test then saw 52 solid pixels outside `sin(1/x)`'s ±1 range, over its 50-pixel limit, and
    the fill fraction at zoom step 60 went 42 % → 61 %.

25. **A fixed aspect must preserve the *scale* on resize, not the range.** The first
    implementation satisfied the ratio by expanding the range (matplotlib's *datalim*): the
    ratio held, but the units-per-pixel changed with the widget, so the curve visibly zoomed
    while a window or a splitter was dragged. `PlotController.setViewport` now scales the
    stored range proportionally to the new size (units per pixel stay put, the visible range
    follows the widget) and only the first size report sets the baseline, so the startup view
    is not scaled. `"view"` mode keeps the old behaviour: the range is fixed and the shape
    follows the widget.

26. **Exporting an image without a visible window.** `QQuickRenderControl.grab()` does not
    exist in PySide6 6.11 (and reading the RHI target by hand is impossible — `QRhi` is not
    bound), while `QQuickItem.grabToImage()` returns a **null** result on a window that was
    never exposed. What works: a `QQuickWidget` with `WA_DontShowOnScreen`, shown offscreen
    at the target size, read back with the synchronous `QQuickWidget.grabFramebuffer()`.
    Verified with `sin(1/x)`, `xlim=ylim=(-1,1)`, 800×200 logical → 1200×300 device pixels
    with the curve stretched 4:1.

27. **The limits *are* the visible range — write the aspect adjustment back.** An earlier
    version derived an "effective" range for drawing and left `xlim`/`ylim` alone, so what the
    app read was not what it saw. The aspect now adjusts the limits in place. Two ordering
    traps found while doing it: (a) the adjustment was applied against the *assumed* viewport
    (800×600) at construction, so the first real size could only expand that stale result and
    the range came out off by the assumed/real ratio — the first *known* size now re-derives
    from the home view instead; (b) `MathPlot.qml`'s default `PlotController {}` receives the
    size reports that fire during `setSource`, i.e. *before* `MathPlotWidget` injects its own
    controller, so the injected one never learned the size and treated the first resize as a
    baseline. The widget now reports the authoritative size from its own `resizeEvent`
    (`Component.onCompleted` sees a 0×0 item anyway).

28. **A QML `property alias` cannot point at a chain.** `PlotView` first declared
    `property alias aspect: root.plot.camera.aspect`; the whole component then failed to load
    with *"Invalid alias reference. An alias reference must be specified as `<id>`,
    `<id>.<property>` or `<id>.<value property>.<property>`"*. Aliases resolve through ids (and
    value types), not through an injected object's members, and the plot *is* an injected
    property (`plot: myPlot`) rather than an id, so no alias form can reach it. Fix: forward
    objects instead (`property QtObject camera: root.plot.camera`) and have the furniture read
    `root.plot.camera.ticks_x` directly; a host writes `view.camera.aspect = 1.0`.

29. **Python tuples do not survive a QVariant into QML.** `Camera.ticks_x` returned
    `list[tuple[float, str]]`; in QML it arrived as an array of the right `length` whose
    elements were opaque (`tick[0]` → `-1`, `JSON.stringify(tick[0])` → `""`), so the grid
    silently drew nothing. Lists index normally (`[[1.0, "a"]]` → `[0][0] == 1`). Fix: the tick
    getters convert through `Camera._qml_pairs()` and expose two-element **lists**; the same
    trap applies to any nested structure (`QVector2D` / `Qt.vector2d` is the other option).

30. **PySide's `Property(..., notify="name")` accepts a string and silently produces a property
    with NO notify signal.** The style properties emitted anyway (the setter emits explicitly),
    but QML bindings never refreshed, and
    `metaObject().property(i).notifySignal().isValid()` is **False** — no error anywhere. Fix:
    pass the `Signal` *object* (`notify=gridColorChanged`, declared above the property in the
    class body). A class-level `Signal` has no `.emit()` and no `.name`, so `Plot._style` emits
    via the descriptor protocol: `notify.__get__(self, type(self)).emit()`.
    Regression: `test_plot.py::test_every_style_property_has_a_wired_notify_signal`.

31. **A Python `QAbstractListModel` subclass cannot be a Qt property type.** Declaring
    `Plot.curves` as `Property(CurveListModel, …)` produced, at class-creation time,
    *"QMetaObjectBuilder: Failed to add property 'curves' to 'Plot': Invalid property type
    'QAbstractListModel*'"* — and QML then saw nothing for `plot.curves`, so the `Repeater`
    created **no** delegates: the plot drew only the grid, no curve at all. Fix: declare the
    property as `QObject`; QML only needs the object to *be* a list model, and
    `roleNames()`/`data()` still drive the delegates.

32. **In Qt 6.11 an ancestor's `Component.onCompleted` runs before its descendants'.** Measured
    (PySide6 6.11.2): the root item's handler ran before the child `PlotView`'s, and before a
    nested `Plot`'s, so a component's default child does **not** exist when an outer handler
    first runs. The Qt Quick explorer's `Component.onCompleted` called `curves.at(0)` on the
    component's default plot and got `IndexError`; worse, a `text: plot.curves.at(0).error`
    *binding* evaluated to an error and, failed bindings never being re-evaluated, stayed empty
    forever. Fix: let the component's default-curve handler act only on an empty model
    (`if (curves.rowCount() === 0) add_curve("sin(x)")`, so a host that adds its own curve
    wins), have a host create its own curve instead of assuming the default one, and update
    chrome imperatively (`draw()` sets the error label) rather than binding through
    `curves.at(0)`.

33. **A QML-declared property typed as a registered Python class cannot be read back from
    Python.** `property Plot plot: defaultPlot` loads and binds fine, but reading it with
    `root.property("plot")` raises `RuntimeError: Can't find converter for 'Plot*'` (a
    `QtObject`-typed forwarder returns the Python object as expected). Fix: read what you need
    from a `QtObject`-typed forwarder (the render smoke test uses `plot.property("camera")`) or
    keep the Python-side reference you injected.

34. **QML chains and the component's default child object.** Two traps that bite a host
    driving `PlotView` from QML:
    * a *chain* cannot be bound: `plot.camera.aspect: 1.0` is rejected outright ("Cannot assign
      to non-existent property 'aspect'"), while `plot.aspect = 1.0` inside
      `Component.onCompleted` works. `Plot` therefore forwards `aspect` (and `xlim`/`ylim`) so
      the common knobs are direct properties; anything deeper needs `Binding { target: … }`.
    * `PlotView` declares `property Plot plot: defaultPlot` with `Plot { id: defaultPlot }` as a
      child object, and QML evaluates some bindings *before* that child exists — a host binding
      on the injected plot (`plot.aspect: 1.0` at the component level) then produces a burst of
      `Cannot read property 'camera' of null` from the component's own bindings. Assigning in
      `Component.onCompleted` is clean. Fixing it properly means making the default model exist
      before the bindings; the obvious attempt (`property Plot plot: DefaultPlot {}` with an
      inline component) is rejected by QML with "Property value set multiple times", so this is
      still open.

35. **Snap screen-space lines to *device* pixels, not logical half-pixels.** The grid is a
    1 px hairline drawn in the Canvas; the axis is 1.07 logical px (`axis_width`, 0.8 pt), i.e.
    1.6 device px at DPR 1.5. The first fix positioned the axis at the exact value
    (`yAxisX - axis_width / 2`), which kept it on the centre column but made its **width**
    flip between one and two device rows as the world position drifted across the pixel grid
    (measured across pans: 1-2 rows). Applying the grid's logical `+0.5` was worse — it put a
    1.6-device line half off the column and the y-axis came out pale. Fix in
    `PlotView.qml`: `_deviceWidth(logical)` rounds the width to an **integral device** width
    and `_snapLine(centre, width)` places the line's edges at integral device pixels. The axis
    is 2 device px at DPR 1.5 and constant; the grid and tick marks use the same helpers.
    Regression: `test_render_smoke.py::test_axis_thickness_is_stable_across_positions` (six
    pans, thickness must be constant) plus the original
    `test_grid_ticks_axes_and_title_are_drawn` dark-pixel check.

36. **The axis is clamped to the nearest edge; the tick labels are clamped too.** With the
    origin off screen the axis sticks to the item's edge (the Desmos behaviour), and the mark
    and the label are positioned from *that* clamped value — otherwise the labels of a pinned
    axis leave the item and the panning user loses the scale. Measured on a 900x600 frame panned
    to y in [-38, -34]: the x-axis sits on the top row with its labels just below it, and the
    edge labels ("-6" at the left) stay fully inside the item. Regression:
    `test_render_smoke.py::test_axis_pins_to_the_edge_when_the_origin_is_off_screen`.

37. **`QQuickWidget.setProperty(name, …)` sets the property on the *widget*, not on the QML
    root.** The offscreen export injected its plot with `widget.setProperty("plot", plot)`; the
    widget is a plain `QObject`-derived `QWidget` with no such property, so the call returned
    False and did nothing, and the render quietly used the component's **own** default model:
    white background, its own `sin(x)`, the default camera — and it looked almost plausible,
    because the image size (which the export sets on the widget) was still right, so the bug
    only showed up in the pixels. Diagnosed by checking the *injected* camera: its viewport
    stayed at the reference 900×600 (no size report ever reached it) and the frame showed a
    curve although the injected plot had none. Fix: `widget.rootObject().setProperty("plot",
    plot)`, exactly as `MathPlotWidget` always did; `QQuickView` was never affected because
    `rootObject()` was used there from the start. Regression: `tests/test_export.py` renders
    through the offscreen widget and checks the requested region's pixels.

38. **`QApplication.instance()` is the `QGuiApplication` in a pure-QML host.** The guard that is
    meant to turn "no QApplication" into a friendly error was written as
    `QApplication.instance() is None`, which is **False** under a bare `QGuiApplication` (the
    singleton is returned, just not as a `QApplication`). The check therefore passed and the
    next line created a `QWidget`, which is **fatal inside Qt**: measured with a
    QGuiApplication-only script, `QWidget()` printed nothing after it and the process exited
    with status 9 — no traceback, nothing to debug from. Fix: `isinstance(QApplication
    .instance(), QApplication)` (the idiom `tests/conftest.py` already used); both
    `MathPlotWidget` (pre-existing) and `Plot.to_image()` use it now, and the export path is
    covered by `tests/test_export.py::test_a_pure_qml_host_gets_a_clear_error` (a subprocess,
    because the test session owns a `QApplication`).

39. **The matplotlib hand-off: two API traps, measured.** (a) `axes.grid(False, color=…)` does
    not turn the grid off — matplotlib warns *"First parameter to grid() is false, but line
    properties are supplied. The grid will be enabled."* and enables it, so the mapping must
    branch (`grid(True, color=…)` / `grid(False)`); found by exporting a plot with `grid=False`
    and seeing a grid. (b) With several expressions, `sympy.plotting.plot(..., line_color=[…])`
    passes the *whole list* to every series (matplotlib then rejects it: *"['#1f77b4', '#ff7f0e']
    is not a valid value for color"*), `rendering_kw=[{…}, {…}]` is silently ignored, and passing
    series objects to `plot()` fails in `sympify`; `sympy.plotting.Plot` does not exist in sympy
    1.14 either (the object `plot(..., show=False)` returns *is* the `MatplotlibBackend`). Fix:
    build with `plot(*expressions, (x, x0, x1), show=False, nb_of_points=…, label=[…])` and set
    the per-series style afterwards — `handle[index].line_color = colour` (the handle is
    indexable) — which is also the documented sympy flow. Minor: a spine's colour reads back as
    an RGBA tuple (`spine.get_edgecolor()`), not the hex string that was set.

40. **A `Property(str, …)` stringifies before the setter sees it.** `Curve.expression` had to
    accept both `"sin(x)"` and `sp.sin(x)`; with the property declared as `str`, PySide converts
    the value *before* calling the setter, so an `sp.Expr` arrived as text and the curve had to
    `sympify` it back (an `Expr -> str -> Expr` round trip on every assignment, and another one in
    `mpl.py`). Declaring the property as `"QVariant"` hands the value over unconverted. Measured
    with a counting `sp.sympify` monkeypatch: `Curve(sp.sin(x))` 0 calls, a string 1, re-setting
    the same expression (string or object) 0 more, `to_matplotlib()`/`to_sympy()` 0.
    `tests/test_curve.py::test_expression_accepts_a_string_and_a_sympy_expression` is the
    regression.

41. **In the 1-D shader, `y` is the pixel's own coordinate — so `x**2 + y**2 - 1` was silently a
    different curve.** The 1-D fragment template declares `float y` (the pixel's vertical
    position) and strokes `y = F(x_i)`, so an expression with *both* symbols was evaluated as
    `F(x, y_pixel)`: the level set of `y - F = 0`. Measured before the fix: `x**2 + y**2 - 1`
    drew a circle centred at **y = +1/2** (`y² - y + x² - 1 = 0` → `(y-½)² + x² = 5/4`),
    `x²/4 + y²/3 - 1` at **y = +3/2**, `x²/4 - y²/3 - 1` at **y = -3/2** — and the stroke width
    varied with the slope of that pseudo-conic. The fix is the template following the expression: `{x}`
    (or nothing) is the 1-D stroke, `{x, y}` is the level set, for both `add_curve` and an
    assignment to `expression` (the explorers' text field turns a function into a circle in
    place), with `ImplicitCurve`/`add_implicit` forcing the level-set template and any other free
    symbol refused (`ValueError` from the API, the same message through `Curve.error` on
    assignment, keeping the curve that was drawn). The implicit template prints the expression with
    two macro parameters — `#define F(x, y) (…)` — which means printing it with
    `func_glsl(expr, "x")` (the `name` argument *substitutes* a single symbol, so passing "x" for
    an expression that already uses x and y is the no-substitution case) and calling the macro
    with the plain pixel coordinates, so no extra parentheses are needed (item 1 is about
    *offset* arguments). Measured after the fix, unit circle in a [-2, 2]² view (900×900 device
    px, DPR 1.5, 1 world unit = 225 px): the centre column lights rows 223..676 (expected 225 and
    675), the 9×9 centre block is blank, and the stroke measures 3.00 / 2.96 / 3.00 / 2.96 device
    pixels at 0°/45°/90°/135° (nominal 3.0, spread 0.04) by the coverage-integral method of item
    24. The Qt export needed no change; the matplotlib hand-off skips implicit curves (a level
    set has no polyline) and says so. (Item 42 later found the export's *default range* — not
    its shader path — did have to change.)

42. **An export's default range must come from the camera, not from the offscreen widget's
    logical size.** `export.render` derived it as `live ± 0.5 · logical · scale`, with
    `logical = width / devicePixelRatio`. The camera's scale is world units per logical pixel
    *of the live view*, so on a scaled display the export was silently **cropped** by the DPR:
    `to_image(width=400, height=400)` of a camera told `setViewport(400, 400)` and given
    `xlim = ylim = (-2.5, 2.5)` (DPR 1.5 → a 267-logical-pixel widget) showed ±1.67, not ±2.5.
    Found through the *implicit* renderer, which is all shader and has no geometry to blame:
    `x**2/4 - y**2/3 - 1` exported **blank** (0 non-white pixels in the whole 400×400 canvas)
    while `x**2/4 + y**2/3 - 1` drew 673, and the two shaders differ by one sign — a hyperbola's
    visible arc needs |x| >= 2, the ellipse's waist reaches |x| = 1.31. The live `QQuickView`
    path drew all three reported failures, so shader and bake were never at fault: patching the
    fragment template to paint its own uniforms showed `camera.z = 0.0125` (right) and
    `size.x = 267` (the widget, right) — the mapping was honest, the *region* was wrong.
    Diagnostics that do **not** work here: `qsb.bake` succeeds for the failing source, and
    `fragColor = vec4(1, 0, 0, 1); return;` fills the export red (the pass runs; the curve's
    `cov` is 0 everywhere because the arc is off screen). Fix: default the range to
    `live_size`, the camera's reported viewport — i.e. exactly `camera.xlim` / `ylim` — so
    `width` / `height` / `dpi` change the resolution only, which is what
    `savefig("hi.png", dpi=2.0)  # the live view, twice the pixels` always claimed and what
    `test_no_arguments_reproduces_the_live_view` pins. Measured after (DPR 1.5, the ±2.5 view at
    400×400, sampled every 2nd pixel): the three failures 461 / 305 / 461; the previously
    "working" ones 513 / 958 / 912 / 862 / 776 / 696 / 1084. Regression:
    `tests/test_implicit.py::test_the_default_export_range_is_the_camera_view` — the sign pair,
    exported at 400×400 *and* at half the pixels, which is the assertion that fails on a DPR-1
    screen too (there the two ranges coincide at 400×400).

43. **pytest's own faulthandler plugin wedges this repo's GUI tests on this machine.**
    `uv run pytest -q` hung indefinitely — six runs (one of them with the shader changes
    stashed), no output at all, no traceback, and a manual
    `faulthandler.dump_traceback_later(75, exit=True)` inside the run never fired either. The
    same render path driven from a plain script (`QQuickView` + `grabToImage`, calling the test
    module's own `_render` helper) finished in ~5 s, and `pytest -p no:faulthandler` passed in
    1.3 s; the full suite then passed (124 tests, 27.7 s) with that flag. So it is the plugin,
    not the shader or Qt: `-p no:faulthandler` is the workaround. A wedged run also leaves
    orphaned `python.exe`/`uv.exe` pairs behind, which then block every later `uv run` — kill
    them with `powershell -Command "Stop-Process -Id <pid> -Force"` (`taskkill` rejects its
    `/F` switch in this MSYS shell).

## Performance (measured)
 ≈ **230 ms** (Windows / D3D11 / 900×600):

| Stage | Cost | Note |
|---|---|---|
| Qt graphics device init (RHI/D3D11) | **~160 ms** | The first window's `show → exposed`; a **bare window without any ShaderEffect is just as slow**, so this is not the shader's fault |
| Window + QML load + first frame | ~80 ms | Measured from the second window on (device already created) |
| `qsb` bake | 0 / ~126 ms | Cached by source hash: 0.15 ms on a hit; a source change re-bakes once |
| Expression analysis (sympy) | 0.6–3 ms | See item 14 |

Frame grab cycles (900×600, `grabToImage` — frame plus readback, an upper bound on the
GPU cost; the fixed-grid shader measured 31 ms of pure GPU at 2400×1500, ~1/7 at 900×600):
`sin(1/x)` default 27 ms, `tan(x)` default 34 ms, `sin(1/x)` at zoom 90 (worst case) 33 ms
mean / 36 ms peak. 1-D fragment shader source ≈ 21 KB.

Per-pixel `F` evaluations (counter patched into the shader, read back out of the rendered
pixels; fixed-grid renderer → march, from item 44):

| expression | fixed grid (mean/peak) | march (mean/peak) |
|---|---|---|
| `tan(x)` | 16.96 / 17 | 18.98 / 50 |
| `x^3` | 16.45 / 17 | 16.53 / 50 |
| `log(x)` | 8.50 / 33 | 7.97 / 50 |
| `1/x` | 14.64 / 33 | 6.27 / 50 |
| `exp(-x*x)*sin(10*x)` | 14.97 / 33 | 6.83 / 58 |
| `sin(1/x)`, default view | 12.56 / 33 | 5.32 / 58 |

The march pays per pixel: 1 `f` + 1 `f'` + the walk. A shallow pixel stops after a few
steps (the escape rule recedes) and never runs the column probe, so it is cheaper than the
old fixed grid's unconditional 8 narrow-window samples; the probe (8 grid + 8 midpoints)
and the wide window are only paid where the march said so. The peak is the banded column:
the full step budget in both directions plus both windows.

## The march (the fixed-x grid replaced)

44. **The fixed-x renderer was replaced by the march** (symptom → cause → fix, with what
    each superseded mechanism cost).

    *Symptoms (the two the rewrite was for):* the stroke thinned out where dy/dx was large
    (2.02 of the nominal 2.25 px at screen slope 200), and vertical connectors kept
    reappearing at discontinuities the analysis could not see. *Cause:* the renderer
    sampled f on a **fixed x grid** but stroked in screen space, and bridged the mismatch
    with case-by-case detection — a ±1-column window widened until the band criteria
    stopped over-triggering (items 5/24), three separate POLE guards plus a 32×viewport gap
    against connectors (item 12), a 16-point interleaved turn count against sampling luck
    (item 11), and measured thresholds tying the two families together. The detection was
    **structurally blind**: `x/sqrt(x**2)` (= sign) has no pole denominator, and it drew a
    **454-px full-height column** at x=0 through every guard, measured.

    *Fix: one mechanism.* The fragment shader walks the curve per pixel: seed at the
    column (probe if it is a pole/domain column), **project the pixel onto the tangent**
    one evaluation to start at its foot, then march **1 logical px of arc length per
    step** in both directions (`f'` bootstraps the slope; 4 halvings when it cannot), stop
    at the viewport edge, at a receding `R+1` ball, or at the 12-step budget. A segment
    joins only samples within **2.5 px** (the join invariant). Consequences, each one
    replacing a family of patches:

    - width is a property of the line, not of the slope: `k·x` measured **3.00/3.00/3.00/
      2.94** device px at slopes 2/10/40/200 (nominal 3.0) — and there is no window to
      widen against band criteria, because coverage is bounded by the escape rule;
    - a connector cannot be *formed*, whatever its origin — the invariant refuses the
      chord, and a pole column is dark because its branches leave through the viewport
      edge before the pole;
    - a jump lands in the invariant too: an honest 1-px stride that still cannot join is a
      discontinuity, and the march resumes on the far branch (`run` is true only for
      stride estimates measured from joined pairs; a probe step halves at most 4 times
      before concluding) — that is why `x/sqrt(x**2)` draws 4 px where the old renderer
      drew 454;
    - the band's (a)/(d) criteria became four march-native triggers, each tapering with
      its count: budget exhausted (sustained structure), ≥ 2 reversals, ≥ 4 missed tangent
      predictions (aliasing), or ≥ 2 column-probe **violations** — the fixed-x probe's
      midpoint landing outside its pair's [min,max], which is the sub-column oscillation
      the march cannot see along the arc. The 16-point interleave and the 16×/4px
      thresholds are gone;
    - the pole-flood hazard got a gate that needs no pole recognition: a pole or jump
      column's envelope brackets the whole viewport without taking those values (its
      window samples form a monotone "V" — exactly one violating pair), while a genuinely
      oversampled oscillation violates most pairs: the fill is **gated on the violation
      count** (`smoothstep(3, 6, violations)`), zoom-stable where the old 32×-viewport gap
      was viewport-relative and the pixel-unit gates were not.

    *Traps re-armed while implementing (measured here, on this machine):*
    - the escape rule must count only **receding** steps: a march closing in on its foot
      from far away (steep curves) was stopped on its way in → dashed strokes;
    - the **tangent-foot seed cap** and the hopless check matter: a seed > ~500 px along
      its own tangent is meaningless, and a seed more than `R + 2 + MARCH` from the pixel
      will not reach it — marching anyway burns the budget and fires a spurious band;
    - a dy smaller than ~1e-4 px is **float noise** whose sign flips read as reversals
      (constant-valued expressions such as `x/sqrt(x**2)` move ~1e-12 per step) — that is
      what banded the jump column solid behind the invariant;
    - the old narrow-window turn counting hid a **second** mix line: after adding the
      violation gate a `cov = mix(cov, band, …)` line survived below the gated one and
      silently undid it.

    *Superseded:* items 6 (two rulers → the probe's violation count), 11 (16-point
    interleave → arc sampling turns are traversed, not luck-sampled), 12 (three POLE
    guards + the 32× gap → the invariant + the violation gate), 24 (±1-column window →
    arc-length marching). Items 5 (segment distance) and 13 (the boundary ray) survive
    unchanged inside the march; the ray remains its own mechanism because a jump over the
    viewport is not an arc a 12-step budget can walk either.

    *Per-pixel `f` evaluations* (counter patched into the shader; mean/peak over 1-in-3
    pixels, fixed grid → march): `tan(x)` 16.96/17 → 18.98/50; `x^3` 16.45/17 → 16.53/50;
    `log(x)` 8.50/33 → 7.97/50; `1/x` 14.64/33 → 6.27/50; `exp(-x*x)*sin(10*x)`
    14.97/33 → 6.83/58; `sin(1/x)` 12.56/33 → 5.32/58. The mean is lower everywhere; the
    peak is the banded column. 1-D fragment source ≈ 21 KB. Frame grab cycles
    (900×600, incl. readback): default-view `sin(1/x)`/`tan(x)` 27/34 ms mean, `sin(1/x)`
    at zoom 90 (worst case) 33 ms mean / 36 ms peak.

45. **A steep monotone wall vanished under the march; the column probe's chords cover
    it** (symptom → cause → fix, with what the post-rewrite review found).

    *Symptoms (user-reported, after item 44):* `log(x)` "thins then thickens at the
    bend at every zoom"; `tan(x)`/`1/x` go thin or disappear at large |y|; `sin(1/x)`
    has a fragmented patch in the middle that stays fragmented when zoomed; Desmos
    fills the screen for `tan(x)` when zoomed in far enough.

    *Investigation (all measured here, on this machine):*
    - the `log` corner is **correct**: run heights at six points across five views match
      the constant-width stroke scaled by the local slope and the AA feather (e.g. a
      6/5/5/4/5/4-row profile over slope 21 → 1.7, where the "expected" single number
      is the run height divided by √(1+m²)). Comparing a run's centre against the
      curve's value *at the pixel's own column* is meaningless on steep stretches —
      that was a measurement artifact, not a render bug.
    - `sin(1/x)`'s centre column is **solid** (900/900 rows above the solid threshold)
      at zooms 30/45/60/90; the fragmentation is the band/stroke boundary columns
      (17 of 1350 at zoom 30), the same transition the old renderer had.
    - `tan(x)`/`1/x` at large |y| **really did vanish**: at y 50..60 over x ±6, `1/x`
      drew 0 columns. Its pixels' feet are a whole view up the arc (no march budget
      can walk there), the tangent projection overshoots past the pole onto another
      part of the branch, and the hoplessness gate then correctly skips the march.
      The only thing that can cover those pixels is the band — and the band's
      violation-count gate (item 44) *blocks* it: a wall's probe samples are monotone
      (the V has exactly one violating pair, at the straddle).

    *Fix: the chord-stroke.* A **consistent pair** of probe samples — one whose
    midpoint lands strictly inside the pair's [min,max] — is a trustworthy chord: the
    connected curve between the pair's ends really does pass through the values in
    between (the jump pair of a pole or jump column just failed the midpoint test;
    a flat or degenerate span brackets nothing). Stroke those chords with the same
    coverage ramp as the march, and let the bracket weight the band fill:
    `cov = max(cov, band · … · max(violations-gate, bracket))`. It is the old
    fixed-x renderer's long-chord coverage, minus its connector: the straddling pair
    never qualifies. Measured after: `1/x` at y 50..60 over x ±6 draws a full-height
    wall (4 columns), `tan(x)` the same at all four of its in-view poles (16 columns),
    and `k·x` keeps 3.00/3.00/3.00/2.94 device px at slopes 2/10/40/200.

    *Traps re-armed while implementing:*
    - the band fill must be `max(cov, …)`, **not** `mix(cov, band, w)`: mixing with a
      band of 0 darkens the stroke (measured: the ray-drawn part of `log`'s descent
      pinched to 2.50 of 3.00 px once a chord bracket outweighed the ray);
    - the probe's 1-px spacing is **load-bearing**: a slope-adaptive (R/|f'|) spacing
      hugs the column's own value and brackets nothing — the large-|y| wall went back
      to 0 columns. The coarse grid is what produces pairs that span the pixel's y.
    - the tang-foot projection's cap (`|t| < 512`) was dead weight, not a guard:
      pixels whose projection lands far are filtered by the hoplessness gate, and a
      pole-adjacent column's own tang-seed already lands in the ball. Removing it
      changed nothing visible but the pole-adjacent behaviour under deep zoom.

    *Known limitation (unchanged by the fix):* the Desmos-style **merged pole wall**
    — two branches a fraction of a pixel apart under an enormous y view (e.g. tan at
    x ±0.002, y ∈ [1e6, 1.1e6]) — is still blank. Its visible arc is the sliver
    between the pixel's column and the pole, narrower than the probe's 1-px step, and
    the tangent projection overshoots the pole. Covering it needs a pole-ward
    bisection seed (solve F(x) = y towards the straddled discontinuity), a separate
    mechanism from the march.

46. **log(1+x)'s descent wall broke in the middle — three stacked holes and a compiler
    trap** (symptom → cause → fix, with the debugging lesson).

    *Symptom (user-reported with a screenshot):* `log(1+x)` zoomed deep into its
    descent (view x [-2.9, 0.7], y [-7.7, -4.8]) drew the wall at x ≈ −1 with a
    ~180-row hole in its middle; `tan`/`1/x` went thin or vanished at large |y|;
    `sin(1/x)` looked speckled; "nothing improved".

    *The three holes:*
    - **out-of-domain pixels took the early return with the ray alone.** A pixel just
      left of the boundary is still inside the curve's stroke band (the curve passes a
      pixel or two inside at that height, where the boundary is not vertical yet), and
      the ray only covers the genuinely vertical part: it starts at the boundary probe.
      Everything between the polyline's reach and the ray's start was dark. Fix: the
      out-of-domain branch now also strokes **just-inside chords** (four samples
      inside the boundary, with "the pair brackets this pixel's height" as the
      consistency test — exactly the monotone near-boundary case that qualifies).
    - **the ray started too low.** `edge_nl` came from a single probe 0.25 px inside
      the boundary — the *deepest* value. A down-ray's top edge should be the
      *highest* in-domain probe: the descent carries every value below it to the
      boundary. Fix: a 4-step ladder on whichever side is in the domain, best_down for
      the down-ray's top (best_up for the up-ray's bottom). The shallow-boundary
      regression the probe exists for (`log(x-1)` in a shallow view) is preserved:
      every probe below the viewport bottom and the ray still declines to draw.
    - **the wide window straddled the domain boundary.** GLSL's log(negative) is
      undefined and this driver returns *finite garbage*; wl/wh picked it up and the
      band's clamp collapsed the whole column to zero. Fix (both parts):
      guard *then* evaluate — never evaluate F outside the domain — and keep the
      loop in the **same inline form as the column probe**: a `t`-indexed variant
      computed out-of-envelope samples under the optimiser even when guarded
      (measured: wh−wl = 4+ world units where the true spread was 3.2). All six F
      sites in the shader now guard before evaluating.

    *The debugging lesson (cost half a day):* **instrumented renders are not the
    shipped shader.** Adding a global to write diagnostics into the output changed the
    coverage itself — same source plus a store, different pixels lit. Diagnoses that
    referenced variables scoped inside the band's `if` failed to compile, and `Curve`
    silently kept the *previous* binary (that is the documented behaviour), so the
    "diagnostic" was a re-render of the old shader. Two rules that came out of it:
    the band's quantities are hoisted out of the trigger's `if` (a shader whose output
    changes under an unrelated edit cannot be debugged), and diagnostics must be
    verified against the plain render's own pixels, never the other way around.

    *Also fixed along the way:* `cov = max(cov, band …)` — mixing with a zero band
    darkened the stroke; the log-corner and sin(1/x) reports did not reproduce
    (run heights matched constant width × slope × feather at five views; the centre
    column was solid at zooms 30-90 — both earlier "gaps" were measurement artifacts:
    run centres compared against the wrong reference, and grid/ticks counted as gaps).

    Measured after, on the reported view: the wall is continuous, the largest internal
    vertical gap in the whole frame is **5 rows** (the ray start's own antialiasing;
    ~180 before), 130/130 tests green, and `k·x` still holds 3.00/3.00/3.00/2.94 at
    slopes 2/10/40/200.

47. **The delta to 46: the ray's gate and its top edge are different quantities.**
    Over-correcting 46 (giving the boundary ray a *higher* start) forked log(1+x)'s
    descent into two parallel walls: the ray at x = −1 beside the curve at x ≈ −0.996
    in a view where the curve's own samples already reach the viewport bottom. The
    down-ray's start (`rtop`) is separate from the samples' deepest point (`nl`) that
    *decides whether to draw at all*: `nl > y_bot` is "the polyline has not reached the
    bottom", and feeding it the highest probe made a redundant ray draw wherever the
    descent was already visible. `_ray_dist` now takes both; `edge_nl` is the ladder's
    minimum (the gate), `edge_top` its maximum (the start). Measured after: one wall in
    the fork view, the deep-descent wall still continuous (5-row worst gap, the ray
    start's own AA), 132/132 green.

48. **The implicit (level-set) renderer gets the two general guards, not special cases**
    (symptom → cause → fix).

    *Symptom (user-reported with two screenshots):* the same set drawn two ways —
    `x+1-E**y` = 0 (the inverse form) rendered `log(1+x)` "perfectly", while `y-log(1+x)` = 0
    was cut off and drew a vertical line at the boundary; `x-tan(y)` "explicitly drew the
    +/-inf connector" between its branches.

    *Cause:* the implicit path evaluated `F` and `|grad F|` pointwise and divided, with no
    notion of a domain or a discontinuity (item 41 deferred exactly this). At a pole `tan(y)`
    -> ±inf, so `|F| -> inf` *and* `|grad F| -> inf`: the quotient `|F|/|grad F|` degenerates
    to a pseudo-zero and the pole's whole neighbourhood comes out "on the set" (measured: a
    full-width line at every pole). At `log`'s domain boundary `log(negative)` is undefined
    and this driver returns *finite garbage* (item 46), which painted the boundary as a
    vertical line.

    *Fix, both symbol-free:*
    - **domain** — the same sympy predicate the 1-D path uses, injected as `DOM(x, y)` and
      applied *before* evaluating F (out-of-domain pixels are dropped; an evaluated-then-
      discarded call still perturbs the optimiser, item 46).
    - **linearisation validity** — the 1-D probe's midpoint-violation test, one dimension up:
      probes at `p ± R·n̂` must change F by a first-order amount with a small second
      difference (`|fp - 2f + fm| <= 0.9·|fp - fm|`). At a discontinuity the probes straddle
      the jump and the second difference is of the order of the change (measured at
      `x - tan(y)`: 771 vs 679); on a circle it is rounding noise.

    *Traps re-armed while implementing (this machine):*
    - the validity test killed the unit circle down to 42 pixels — `F`'s macro body is printed
      **bare** (`x*x`, no `(u)` wrap), so `F(x + stepx, y + stepy)` expands textually as
      `x + stepx*x + stepx` (AGENTS item 1, re-armed). The probe coordinates are now locals
      (`pxp`, `pyp`, ...) — single tokens, safe under any body form.
    - an instrumented copy of the shader (`vec3 _dbg = ...`) again disagreed pixel-for-pixel
      with the shipped one (item 46): the diagnostics lied *both* ways. All the probe
      quantities are now computed unconditionally, outside the coverage `if`.
    - the diagnostics' failure mode was silent: a probe that referenced an `if`-scoped
      variable failed to compile and `Curve` kept the previous binary, so the "diagnostic"
      was a re-render of the old shader. The circle's 4x-band misbehaviour and the
      as-yet-unexplained ~6-column boundary strip both came from such runs.

    *State after:* the circle is pixel-identical to the pre-guard render (7748 device px),
    both `tan` poles draw 0 pixels, `x-atan(y)` unchanged, `y-log(1+x)` draws no garbage and
    covers its full true extent, and the only residue is a ~6-column dark strip at the domain
    boundary (the probes cross out of the domain there). The user's inverse-form route
    (`x+1-E**y`, `x-tan(y)` -> `y = atan(x)` by symmetry of the formulation) has no such
    boundary, which is why it renders "perfectly" — that is a property of the formulation,
    not a special case. Tests: 134 green, incl. `test_pole_of_tan_does_not_bridge_the_branches`
    and `test_domain_boundary_draws_no_garbage_line`. The dark boundary strip is superseded by
    item 49.

49. **The implicit renderer clipped the stroke at the domain, and the
    second-difference test did not certify a first-order distance** (symptom → cause → fix,
    with the measurements that settled it).

    *Symptoms (user-reported):* `x - log(y-1)` thins and then vanishes as the curve
    approaches `y = 1` (visible from `x ≈ -4` to `-5`); `y - log(x)` does the same toward
    `x = 0` in a deep view; `x - tan(y)` draws a connector through its poles in a wide
    y view.

    *Causes, three different ones:*
    - **the domain clipped the stroke, not the curve.** `!DOM(x, y)` returned transparent,
      and the two-sided validity probes rejected an inside pixel whenever one probe crossed
      the boundary. But a stroke is a band around the level set: at `x = -5`,
      `x - log(y-1)` is `1.0 px` above `y = 1` (y scale `4/600`), i.e. inside the `1.25 px`
      stroke radius — the early return left only the upper ~0.25 px. That is the thinning
      and disappearance; the same geometry applies to `y - log(x)` as `x -> 0+`.
    - **`|F|/|grad F|` is a pseudo-zero at a *diverging boundary* even where the level set is
      far.** At `x = -2`, `y = 1.0001`: `F = 7.21`, the screen gradient is `66.7`, so
      `d = 0.11 px`; the old one-sided path would paint a horizontal line at `y = 1`
      although the curve there is at `y = 1.135`.
    - **a simple pole is monotone across the jump, so three finite differences can look
      linear.** At `x = -6`, `y = 1.60` in a `y ∈ [-20, 20]` view
      (`camera.w = 0.0222`), the old criterion accepted points whose true branch is
      `~6.1 px` away; an offline scan of the same formulas found accepted points up to
      `~143 px` away near the view edges. The connector's width scales with the y view,
      which is why the old ±4-row test at `y ∈ [-4, 4]` passed.

    *Fix, two general mechanisms + one exact guard:*
    - **one-sided boundary extension** (outside pixels): search 8 directions × radii
      `0.5R, R, 1.5R` for an in-domain sample `q`, project `q` onto the level set with two
      damped Newton steps (a step that leaves the domain is halved up to 4 times), require
      the residual `|F| <= 0.25 R |grad F|` and a bounded step, then use the distance from
      the pixel to the projected point. This paints the stroke's outer half and rejects a
      boundary line far from the curve.
    - **projection certificate** (inside pixels): the same two damped Newton steps from the
      pixel replace the second-difference validity test. The residual, not the first-order
      quotient, decides. This is the 1-D tangent-foot seed one dimension up.
    - **pole factor guard**: `_pole_glsl()` injects `POLE(x, y)` (the product of
      `_pole_factors`; `cos(y)` for `tan`). A normal probe with
      `POLE(p) * POLE(p ± R n̂) <= 0` crossed a singularity and is rejected.

    *Measured after* (1350×900, DPR 1.5): at `x = -5` the `x - log(y-1)` column lights rows
    222..225 — both sides of the boundary row (the curve is at 223.5) — and `x = -6` reads
    `(31, 119, 180)` on the curve's own row; `y - log(x)` in
    `x ∈ [-0.001, 0.001], y ∈ [-16, -13]` draws a full-height (900/900 rows) 3-column stroke
    straddling `x = 0` down to the bottom row; `x - tan(y)` with `y ∈ [-20, 20]` has no lit
    pixel farther than `4 px` from the true branch (sampled every 2nd pixel) and 0 lit pixels
    in the ±2 rows around each pole. The old far-out `y - log(1+x)` garbage line stays gone:
    its first lit column is one stroke radius left of the boundary, not a full-height line.
    The unit-circle width/geometry tests are unchanged. Full suite: **138 green**.

    *Regressions:* `test_implicit.py::test_tan_pole_has_no_connector_in_a_wide_y_view`,
    `::test_tan_has_no_far_pixels_in_a_wide_y_view`,
    `::test_a_domain_boundary_keeps_the_stroke_on_both_sides`,
    `::test_the_deep_log_descent_reaches_the_boundary`.

    *Still open:* the 1-D `log(x)` boundary ray (item 13) remains a separate, symbol-derived
    special case; the principled replacement is interval/limit evaluation of the column, not
    another patch to the ray. **Item 50 delivers that for supported expressions** (the ray
    survives only in the legacy fallback `USE_INTERVAL 0`).

50. **The 1-D band is now a GeoGebra-style interval column** (symptom → cause → fix, with
    the traps that cost the rewrite).

    *Symptoms:* `sin(1/x)`'s transition columns overfilled by **1.66 world units** (true
    range at x=0.0844 is `[-0.968,-0.066]`, the ±8-column window painted `[-1.007,0.944]`);
    `x**(1/3)` drew only the right branch (GLSL `pow(negative, 1/3)` is NaN); the
    `log(x)` deep descent depended on a hard-coded vertical ray (`unbounded_edges`), which
    is the "dog's-tail" patch the user rejected; domain endpoints were clipped by half a
    stroke.

    *Fix:* `src/qmlmathplot/interval.py` generates `IVal FI(xlo,xhi)` from sympy. It
    implements the pieces of GeoGebra's `org.geogebra.common.kernel.interval` a fragment
    shader needs: `IntervalSet` topology (empty / connected / inverted gap / whole),
    `+ - * /`, integer and rational powers, `sqrt`, `exp`, `log`, `sin/cos/tan`,
    `asin/acos/atan`, `abs`, `sinh/cosh/tanh`. The shader evaluates the pixel's **column**
    (`x ± 0.5dx`) for the band, the **stroke footprint** (`x ± R dx`) as the march gate, and
    the **neighbouring columns** (`x ± dx`) for the column-segment stroke. Four `IVal` trees
    per pixel. The band is the enclosure plus a `BW = R-0.75` feather; the march remains for
    the smooth curve's constant width. GeoGebra strokes its per-column polyline with the
    vector line width; a pixel's own column segment is at its own x (distance 0) and cannot
    paint its neighbours, so the shader joins the **neighbouring column intervals** with
    diagonal chords and strokes those; a diverging column (infinite interval bound: `log`'s
    boundary, `1/x**2`) strokes the vertical column segments instead. A finite monotone
    column's vertical band is zeroed (those pixels are the chord/march); an oscillatory
    column keeps the exact band. This is what gives a steep wall its width without the
    vertical-band staircase (see measurements). A wide finite connected column runs the
    midpoint probe only as an interval-dependency check; a dependent quotient
    (`has_dependent_quotient`) is left to the march. For
    `x**(1/3)` the scalar printer emits `_real_pow` (odd roots), `x**(2/3)` emits
    `pow(abs(x), …)`, and `_domain_conditions` maps fractional exponents to the right
    half-open domain (this also fixed `x**0.5`, whose Float exponent previously slipped
    past the `Rational(1,2)` check).

    *Measurements:* `sin(1/x)` transition overfill **0.0099 world** (all thresholds 20/100/
    150); `x**(1/3)` left/right lit 860/867; `x/sqrt(x**2)` worst column **4 px** (not a
    connector); `log(x)` descent measures **3.00 device px at every sampled row** without the
    ray; `1/x`/`tan(x)` in a y∈[50,60] view are **3 full columns** (2 before the neighbour
    segments); `tan(x)` with `x∈[-1e15,1e15]` fills every pixel (interval `whole`); full suite
    **144 green**, including the new `test_undersampled_column_fills_envelope_within_pm1`,
    `test_fractional_power_draws_the_negative_branch`,
    `test_fractional_powers_use_the_real_branch`,
    `test_the_interval_backend_is_chosen_only_when_supported`,
    `test_steep_wall_at_large_y_is_drawn_and_keeps_its_width`.

    *Traps re-armed while rewriting (measured here):*
    - **GLSL forbids recursion**, and Qt's `qsb` reports it only as `QSpirvCompiler: Link
      failed` with no detail — after a successful parse. `i_mul` called itself for an
      inverted second operand; several interval helpers (`i_asin`, `i_log`, `i_nroot`,
      `i_pow_int`, `i_sinh`) recursed through their inverted branch. Fix: every operation
      has a `_conn` helper and the wrappers split inverted sets into two rays, so the call
      graph is acyclic. Diagnosed by stubbing functions from the end and bisecting (a
      stub library with all bodies replaced still failed until `i_empty`'s stub was made
      non-recursive).
    - **`#define`s for `#if` must precede the first function definition** in the qsb
      preprocessor; placing `#define USE_INTERVAL` after `_seg_dist` produced
      `#if: bad expression` at the `#if` line. They now sit immediately after the uniform
      block (which also keeps the implicit/1-D uniform-block test passing).
    - **`float && true` is invalid** in GLSL: injecting `1`/`0` for a bool macro failed with
      "wrong operand types: bool && int". Inject `true`/`false`.
    - **The stroke gate must not be the pixel's own column.** Using it on oscillatory/steep
      columns rejected the perpendicular stroke's outer pixels (the stroke reaches a
      neighbour segment; the column's y-range does not), which is the reported thinning at
      large dy/dx. The gate is the **stroke footprint**; the band stays the pixel's column.
      (A monotone footprint *band* was also tried: it made steep straight lines thicker —
      the slope widths went 1.33/1.17/1.14/1.11 — so it is not the vector stroke.)
    - **A column segment at the pixel's own x cannot paint its neighbours** (distance 0 in
      that column). Evaluating `prev = FI(x-1.5dx, x-0.5dx)` and `next` and stroking the
      **chord** between their ranges is the correct GPU analogue of GeoGebra's
      vector-stroked per-column polyline; it took the `1/x`/`tan` wall from 2 to 3 columns
      and `log` to 3.00 px. The vertical column band itself is the staircase: its left/right
      edge moves by one column at a time. Zero it for finite monotone columns; keep it only
      for oscillatory envelopes and stroke vertical segments for infinite (diverging)
      columns, where no finite chord endpoint exists. Clamp the infinite chord endpoints to
      ±1e12 before `_seg_dist`: the dot product of two 1e30 coordinates is inf and the
      coverage collapses to NaN (measured as a 184-row hole in `log(1+x)`).
    - **Interval dependency is real**: `x/sqrt(x**2)` near 0 gives `[-3,-0.33]` for a
      column that is entirely negative (true value −1) and `[-6e27,6e27]` for a straddling
      column; the midpoint probe alone does not catch the mild case (violations 0, sample
      span 2). The band is therefore suppressed entirely for a dependent quotient without a
      sign pole; the march's join invariant draws the 4-px jump. `sin(1/x)` / `sin(1/x²)`
      are not dependent quotients and keep their interval bands.
    - **The scalar path still needs the domain**: the interval band does not replace
      `DOM`/`DOMS` for the march; `log(negative)` returns finite garbage on this driver.
    - The full 1-D shader is ~40 KB with the library (legacy ~29 KB).


51. **The fixed-x sampled polyline returned; the interval band stayed** (the march is now the
    legacy path only). Items 44 and 45 described the march as the renderer; that is no longer
    true for supported expressions.

    *Why:* the march's 1-px-arc segments were too short for the perpendicular foot on a steep
    branch, and it needed the gate, the chord-stroke, the probe, the budget and the tangent-foot
    seed to cover what a wider fixed-x window covers directly. User reports (log wall thinning,
    tan disappearing at large |y|, `sin(1/x)` centre band unchanged) came from those
    compensations interacting.

    *Fix (in `FRAGMENT_TEMPLATE`, `#if USE_INTERVAL`):*
    - **Stroke** — 16 samples over **±2 columns** (0.25 logical-px spacing), joined into a
      polyline and stroked by distance to the nearest segment. ±2 covers the perpendicular
      foot (the old ±1 measured 2.02 of 2.25 at screen slope 200); the sub-pixel vertices give
      the smooth edge. No march, tangent projection, escape rule, gate or chord: the march
      block is inside `#if !USE_INTERVAL`.
    - **Band** — the exact `FI` column, filled only for an oscillation or a divergence/inverted/
      whole topology. The oscillation signal is **reversals inside the pixel's own column**
      (`|t| <= 0.5` of the 16-sample window), not the whole ±2 window. The fill weight is
      `smoothstep(1.5, 3.0, sturns)` so the band's edge feathers instead of cutting a hard
      column; measured at the default `sin(1/x)` view the full-height part is **20 device
      columns**, the same 20 the interval reference implementation produced.
    - **Poles** — a segment is skipped when `POLE(prev_x) * POLE(xi) <= 0`. The macro is
      printed with the substitution name (`_pole_glsl(expr, "u")`); printing it with the
      default `x` left `POLE(prev_x)` expanding to the pixel's own x, so `1/x` still drew a
      full-height connector (measured 900 lit rows at x=0) until the argument was fixed.
    - **Jumps** — for `HAS_DEPENDENT_QUOTIENT`, a segment with a screen jump > `JOIN` is
      skipped. `x/sqrt(x**2)` keeps its ±1 branches and has no connector.
    - **Descent** — GeoGebra's own unbounded-column rule
      (`IntervalPathPlotterImpl.leftToBottom` / `leftToTop`): a connected enclosure with an
      infinite bound is one segment from `(x − 0.5·dx, finite bound)` at the column's left
      edge to `(x, viewport edge)` at its middle. No boundary-x ray and no band fill for a
      divergence; the interval path is a stroked line. This also fixed the descent's top row
      (2.78 → 3.71 of 3.00 device px; the width test no longer skips it).

    *Traps re-armed:*
    - the ±2 sample window must not decide the band: counting its reversals banded every column
      of `sin(1/x)` at zoom 30 — measured as full-height stripes across the viewport. Only the
      central column's reversals are the sub-column test.
    - the width test's first row is the polyline endpoint above the ray (2.78 of 3.00); it is
      now skipped, and the ray-drawn rows measure 3.00-3.69.
    - `sin(1/x)` at zoom 30/60 is a vertical zebra because the test's `zoom_by` shrinks y as
      well (the ±1 amplitude is then far outside the viewport); the vertical stripes are the
      zero crossings, not an artifact. The band fills only the central sub-column region.

    *Measured after:* all 144 tests green; steady-state grab 900×600: `sin(1/x)` default
    13.6 ms, `tan(x)` default 12.4 ms, `sin(1/x)` zoom 90 12.5 ms (march renderer: 27/34/33
    ms); `tan` at y ∈ [50,60] draws four full-height walls; `log(1+x)`'s descent 3.00 px at
    every sampled row; `k·x` 3.00/3.00/3.00/2.94 at slopes 2/10/40/200.

52. **The 1-D interval path now follows GeoGebra's topology rules, not just its enclosure**
    (found by diffing against `euclidian/plot/interval`; all GPU, no tuple list).

    *Deviations found and fixed:*
    - a connected sub-column oscillation was a **filled rectangle**; GGB's
      `DrawInterval.drawJoined` is a **stroked interval path**: the vertical join at `x.low`
      plus the diagonal across the column (`drawUp`/`drawDown` chosen by `high`), and a
      vertical at `x.high` for an independent column. The shader now strokes those segments
      (weighted by `smoothstep(1.5, 3.0, sturns)`).
    - inverted columns were filled top/bottom rays; `DrawInvertedInterval` draws the **top
      segment at x.high** and the **bottom at x.low**, from the gap to the viewport edge.
    - whole columns were filled; `DrawInterval.drawWhole` is one vertical at `x.low`.
    - `leftToBottom`/`leftToTop` were unconditional; `IntervalPath.drawNormalInfinity` draws
      only when the finite bound is on screen. The deep `log` view still needs a line: its
      column interval covers the viewport while the finite bound is above it, so the shader
      treats that column as `whole` (vertical at `x.low`). Without that, the deep-descent
      render test goes to 0 lit pixels (GGB's normal branch declines).
    - `drawUp`/`drawDown` compare the **high** edges (`Interval.isGreaterThan`), matched.

    *Kept deliberately, not copied:* the Java `JoinLines.inverted` neighbour decisions and
    the `IntervalTupleList`; the fixed-x 16-sample polyline supersamples the one interval per
    column for resolved stretches; a dependent quotient's whole column stays suppressed
    (`x/sqrt(x**2)`).

    *Measured after:* 144 tests green; `tan`/`1/x` branches smooth with no pole fill;
    `sin(1/x)`'s band is now the GGB interval path (vertical joins + diagonals) instead of a
    rectangle; `log`'s descent is one segment/column; `tan` at y ∈ [50,60] still four
    full-height walls.

53. **The implicit shader gets GeoGebra's four-corner marching cell and singular guard** (not
    its CPU quadtree/Bernstein/contour machinery).

    *Borrowed:*
    - each pixel is one marching cell: four `F` corner values, only when all four are in the
      domain and no pole factor changes sign between them; edge crossings are interpolated and
      the cell's contour segment stroked (saddle pairing by the centre sign). It is combined
      with the existing `|F|/|∇F|` + Newton projection by `max`, so the projection still owns
      the sub-pixel width and the cell term covers a thin/tangent branch whose centre
      projection fails.
    - `|Fx| + |Fy| < 1e-3` (world units, GGB's singular test) plus `|F| < 0.05` draws a small
      disc for an isolated point.

    *Traps re-armed while implementing:*
    - the implicit template had no `_seg_dist`; the 1-D helper is not shared, so the first bake
      failed with `'_seg_dist' : no matching overloaded function found`. The helper is now in
      both templates.
    - the singular threshold must use the **world** derivative (`|FX| + |FY|`). Using the
      per-pixel `gnorm` made every ordinary circle singular (its gradient is ~0.01 per pixel):
      measured width at 0° became **12 px** against the nominal 3.
    - the cell term is skipped across a pole factor sign change; otherwise a pole's jump would
      be interpolated as a contour.

    *Not ported:* Bernstein subdivision (polynomial-only), adaptive quad-tree refinement, and
    the `ContourAssembler`/`ContourLinker` (vector-path selection, not rasterisation). A closed
    loop entirely inside one pixel with no corner sign change remains outside the cell test.

    *Measured:* 145 tests green; unit-circle widths 0°/45°/90°/135° still 3.0 device px;
    `x-tan(y)`/`y-log(...)` guards unchanged; `x²+y²` draws its point; the full suite passed
    on a rerun (the first run after the change had a one-off partial circle image; not
    reproduced).

54. **`QQuickWidget.grabFramebuffer()` can run before QML `Canvas` furniture has painted.**
    The offscreen export read back clean scene-graph items (curves, spines, ticks, labels,
    annotations, legend) but a `Canvas`-painted grid or guide was missing. Measured: a grid
    image was **pixel-identical to the grid-off image** (diff 0); after draining the queued
    paint with three `QCoreApplication.processEvents()` calls the diff was **9696 px**, and a
    green `add_hline` went from 0 to **1200** pixels. A sleep is not needed; event processing
    is. `Canvas.renderStrategy: Canvas.Immediate` alone did **not** fix it. The export now
    drains events before `grabFramebuffer()` (see `export.render`).

55. **Sampling count: 14, one tier for live view and export.** The fixed-x polyline sampled
    16 points over ±2 columns; the render tests were used to find the smallest count that keeps
    the correctness guarantees:
    - **8** samples: `sin(1/x)` transition overfill **0.273** world units (limit 0.05), the
      `log(1+x)` descent breaks with a **334-row** gap, and the descent width falls to
      **2.00** of 3.00 px.
    - **12** (center-aligned): transition overfill **0.064** — still over the limit.
    - **14** (center-aligned): all render smoke tests pass.
    So the constant is `NS = 14` (0.286-column spacing) everywhere. A two-tier
    "half the samples while dragging" mode was implemented and then removed: it was visible as
    a quality change during interaction and the user rejected it. Anti-aliasing is the analytic
    coverage ramp, not the sample count, so reducing samples does not change AA quality much —
    it changes how well the polyline follows steep/oscillating geometry.

56. **A conservative ±2-column interval rejects background pixels before sampling.** Dragging
    re-renders the whole fragment pass every frame; the per-pixel 14-sample loop dominated even
    where no curve was near. The shader now evaluates `wide = FI(x - 2dx, x + 2dx)` first: every
    sample/segment it uses lies in that window, so a pixel whose `y` is farther than
    `R * camera.w` from `[wide.lo, wide.hi]` cannot be painted. `wide` empty returns
    transparent; whole/infinite columns do not reject (the envelope may fill the viewport).
    The reject is before `own`/`gate`, so skipped pixels also avoid those interval evaluations.
    Measured by alternating two live 900×600 views with and without the block, 15 pans each
    (`grabToImage`, includes readback): `sin(x)` **23.7 → 22.2 ms**, `sin(1/x)` **23.6 → 21.0
    ms**, `x**2` **22.7 → 20.4 ms**. This is not a column cache: every row in a column still
    recomputes the same samples. The larger win would be a pre-pass that stores the 14 samples
    (and the interval topology) per column in a texture; the reserve is the render architecture.

57. **A windowed/frozen app must launch `qsb.exe` with `CREATE_NO_WINDOW`.** Measured: the
    PySide6 `qsb.exe` PE subsystem is **3** (console), so from a host with no console
    (`pythonw.exe`, PyInstaller `--windowed`/`--noconsole`) Windows allocates a console for the
    child and a black cmd window flashes for the bake (~126 ms on a cold cache).
    `capture_output=True` does **not** prevent that — it only redirects the streams. Fix:
    `subprocess.run(..., creationflags=subprocess.CREATE_NO_WINDOW)` in `qsb.bake()` (Windows
    only; the constant is `0` elsewhere). The child still gets a console, just an invisible one,
    so its own console children inherit the hidden console. A cache hit never starts qsb at all.

58. **The stroke-footprint interval is evaluated only for an empty column.** `gate` widens the
    column by the stroke radius so a pixel just outside the domain still sees the band, but
    that matters only when `own` is empty; every other pixel used to pay the extra `FI`.
    `gate` is now computed inside `if (col.kind == IK_EMPTY)`. Measured by alternating two live
    900×600 views, 15 pans each (`grabToImage`, includes readback): `sin(x)` **17.0 → 16.4
    ms**, `sin(1/x)` **17.8 → 17.6 ms**, `x**2` **16.8 → 16.8 ms**. The common path now does
    two interval evaluations (`wide`, `own`) instead of three; the pole/domain path is
    unchanged.


59. **The singular-cell test needs a scale-invariant distance gate.** Reported on
    `(x+y)/(x*y) = 0` (the line `y = -x`): the far quadrants around |x|,|y| ≈ 50 were filled
    solid. Cause: the isolated-point branch accepted `|Fx| + |Fy| < 1e-3` **and** `|F| < 0.05`,
    but for this expression both are true over huge regions — F and its gradient are tiny far
    from the line, although the set is far away. Scaling the expression (`1/x+1/y` vs
    `(x+y)/(x*y)`) changes the absolute thresholds without changing the geometry. Fix: also
    require `|F| < R * |grad F|` (pixels), i.e. the point must be on the set to first order.
    The ratio is scale-invariant; an isolated point such as `x² + y² = 0` still passes because
    both sides vanish there (measured: its centre is still painted). Regression:
    `test_implicit.py::test_a_scaled_rational_level_set_does_not_fill_far_fields` and
    `::test_an_isolated_singular_point_is_still_drawn`; the former samples (±50, ±50) and
    (±90, ±30), which were solid before the fix and are background after it, while (50, −50)
    on the line remains lit.

60. **A pole wrapped by a monotone map still has to be a gap.** `exp(tan(x))` was drawn as a
    continuous curve: at a pole column `tan` is `whole`, `exp` maps it to the connected interval
    `[0, +inf]`, so the normal-infinity path drew a vertical connector from 0 to the viewport
    top, joining the two one-sided limits (`exp(tan)` → 0 from the left, → +inf from the right).
    Fix: compute `pole_col = HAS_SIGN_POLE && POLE(x - 0.5dx) * POLE(x + 0.5dx) <= 0` and
    suppress the connected interval paths and the band for that column. The sample polyline
    already refuses the crossing segment, so the branches remain separate. Regression:
    `test_render_smoke.py::test_asymptote_is_not_connected` now includes `exp(tan(x))`; its
    pole neighbourhood has no full-height connector.

61. **Dense pole columns must degrade to a fill, but a single pole must stay a gap.**
    At a zoom where one logical column spans many `tan` periods, `exp(tan(x))` was still a
    forest of one-sample stripes: the first pole-gap fix suppressed the connected interval path
    for every pole column, which is right for one pole and wrong for an undersampled column.
    The band's `sturns` is not a reliable detector here (a column of ~1000 poles can alias).
    Fix: count sign changes of the expression's pole factor at 32 quasi-random x positions
    across the sample window (only when `|camera.z| > 1`, so normal views pay nothing), and
    when at least one crossing exists and the column is undersampled, fill its attained y
    interval (`dense`). Normal views (`camera.z` ≈ 0.013) keep the branch gap. Regression:
    `test_render_smoke.py::test_dense_pole_columns_degrade_to_fill` (the reported 1.6e6-wide
    view: 900/900 columns filled) and `::test_asymptote_is_not_connected` (normal `exp(tan(x))`
    still has no connector at its poles).

62. **The sampled polyline and a column chord are unioned, not switched.** At large |y| `tan`
    and `log` thinned just before the band filled: the sampled polyline's endpoint/perpendicular
    coverage was narrower than the interval column. The interval renderer now adds, for a
    resolving connected column, one chord `(x-0.5dx, y0) -> (x+0.5dx, y1)` (orientation from
    the first/last valid sample) and takes `min(pmin, chord_dist)` with the same radius. This
    is not GeoGebra's single-path renderer (the sample polyline still exists), but it removes
    the pre-fill width drop without a mode switch. Measured `tan(x)` at y∈[-200,200]: worst row
    **2.00** of the 3.00 device px (was 1.7); `log(x)` 3.00 on every sampled row. Regression:
    `test_render_smoke.py::test_tan_branch_width_does_not_collapse_at_large_y`.

63. **Neighbour joins close the hand-off between column chords.** The column chord fixed the
    width of a steep column itself, but adjacent chords ended at different values at the shared
    column boundary, leaving a 1-px step (visible as slight roughness on `log`). For a steep
    connected column, also stroke the short vertical join at each boundary from the neighbour's
    endpoint to this column's endpoint, with the same radius. Measured `tan(x)` at
    y∈[-200,200]: every sampled row is now 3.00–3.98 device px (min 3.00; it was 2.00); `log(x)`
    is 3.00 on every sampled row. The join is only evaluated when the column span exceeds
    2R, so a normal `sin(x)` pays no extra `FI` calls.

64. **One `QQuickRhiItem` can replace every `ShaderEffect` from pure Python** (PySide6 6.12;
    measured, no C++ plugin). `QQuickRhiItemRenderer` is subclassable and its
    `initialize`/`synchronize`/`render` overrides are called on the scene-graph thread; the
    item is registered as a normal QML type with `qmlRegisterType`. The renderer reads a
    **snapshot** of the curve list (taken in the item's model-signal handler on the GUI
    thread), creates one pipeline per fragment shader and one dynamic 128-byte uniform buffer
    per row, then draws a full-screen triangle per curve in a single `beginPass`/`endPass`.
    Two curves at 900x600: grab+readback 12.3 ms mean, versus 13.6 ms for the old
    one-curve ShaderEffect path.

    Traps measured while moving:
    - the curve's existing `vertexShader` is ShaderEffect's `qt_Vertex`/`qt_MultiTexCoord0`
      shader. Feed it to a pipeline with no vertex input and **nothing is drawn** (the
      first blank integration looked like a compositing/layering bug). The renderer uses
      its own `gl_VertexIndex` full-screen triangle; only the fragment shader comes from
      `Curve`.
    - Python callbacks on the scene-graph thread need the GIL. A blocking
      `QTest.qWaitForWindowExposed(view)` holds it and deadlocks before exposure; the GUI
      tests pump `app.processEvents()` until `view.isExposed()` instead. This also applies
      to any host that blocks the GUI thread waiting for a first frame.
    - `QRhiCommandBuffer.beginPass` in PySide is
      `(rt, colorClearValue, depthStencilClearValue, resourceUpdates=None, flags=...)` —
      the C++ `clearPos`/`clearRect` form is not exposed; `QRhiDepthStencilClearValue(1,0)`
      is required.
    - PySide does **not** return the batch passed to `beginPass` to QRhi's pool, despite
      the C++ documentation saying the pass implicitly destroys it: after 64 frames the
      warning `Resource update batch pool exhausted (max is 64)` appears while zooming.
      Request one batch per `render()`, keep it in a local, call `used.release()` after
      `endPass()`, and never hold a batch between frames (an `initialize()` batch is leaked
      when the renderer is re-created). Regression (subprocess):
      `test_zooming_does_not_exhaust_the_update_batch_pool`.
    - `QRhi.newBuffer(type, usage, size)`; a `Dynamic` uniform buffer creates and accepts
      `updateDynamicBuffer`. `Immutable` returned `False` from `create()` on this D3D11
      driver with a 16-byte buffer.
    - blending must be premultiplied (`One`, `OneMinusSrcAlpha`), because the fragment
      shader writes `vec4(color.rgb * a, a)`; the item has `alphaBlending = True`.
    - in QML, writing `property vector4d camera: ...` on the delegate **declares a new
      local property** and silently shadows the Python property; assign without
      `property` when the type already exposes it.
    - model changes are snapshotted in `_refresh()` and signalled to the item with
      `update()`; the render thread never calls `CurveListModel.at()` while the GUI may
      mutate it.
    - `PlotCurves.qml` must `import QmlMathPlot 1.0`; the single item is registered there.
    Regressions: `tests/test_rhi.py` (all curves drawn, visibility/colour updates, a curve
    added after exposure).

65. **One dynamic-offset uniform buffer per plot, not one per curve.** The single RHI pass
    packs every curve's 128-byte block into one `QRhiBuffer` with `rhi.ubufAlignment()`
    strides and binds it through one `uniformBufferWithDynamicOffset` SRB. PySide's
    `setShaderResources` takes the offsets as `setShaderResources(srb, 1, (0, offset))`
    (the C++ array form is not exposed). The per-row uniform bytes are compared with the
    previous frame and only changed rows are uploaded, so a style/focus repaint does not
    re-upload the camera block of every curve.

66. **The GGB path renderer is a single `GeneralPath`; the prototype is `PathCurveItem`.**
    GeoGebra's `IntervalPathPlotterImpl` (`D:/Projects/geogebra/source/shared/common/src/main/
    java/org/geogebra/common/euclidian/plot/interval/`) appends every column to **one**
    `GeneralPathClipped`: `DrawInterval.drawJoined` continues with `lineTo`, never starting
    a second path per column. That is why GGB has no per-column seam; our shader draws each
    pixel's local path and unions two such paths (sample polyline + interval chord), which
    leaves a 1-px fringe/step on tan and log.

    `src/qmlmathplot/pathcurve.py` is the standalone prototype of the GGB shape: one
    `QPainterPath`, one `QPen` (round cap/join), sampled at 4 points per logical column.
    A pole is a sign flip with a large jump or `|y| > 1e5`; a domain exception / non-finite
    value starts a new subpath. Measured images: tan branches are continuous with no
    pole connector, log has no per-column step or parallel fringe. Regressions:
    `tests/test_pathcurve.py` (tan pole column is not bridged; log draws nothing left of
    x = 0). It is deliberately **not** wired into `PlotView` yet: QQuickPaintedItem is a
    CPU raster path, while the production curves stay on the RHI shader until the
    geometry/vertex-buffer port is ready.

67. **`CurveRhiItem` draws `tan(x)` and `log(x)` with a compute-shader interval path.**
    `ggb_compute.py` generates one compute shader per expression: a single invocation loops
    the screen columns in order, evaluates `FI(x0, x1)` (the existing interval GLSL), and
    emits up to two quads per column into a `StorageBuffer | VertexBuffer`. A tiny
    vertex/fragment pipeline then draws the buffer. This is the sequential shape of GGB's
    `IntervalPath` rather than independent per-column vertex instances; no CPU sampling is
    involved.

    Geometry rules: `IK_WHOLE` is one full-column quad; `IK_INVERTED` emits the top ray at
    `x.high` and the bottom ray at `x.low`; `IK_CONNECTED` uses the interval endpoints, with
    the scalar `F` only deciding the up/down orientation; empty columns are degenerate.
    One interval per screen column, matching GGB's `FunctionSampler` (`bounds.getWidth()`).
    The earlier 8-subcolumn variant reduced stair-steps but was not GGB's path semantics. `abs(camera.z) > 1.0` is
    the undersampled case (several poles per column) and fills each non-empty column, which
    is the dense behaviour GGB's interval branch gives.

    Traps measured while wiring it:
    - qsb has no `comp` in `--qt6`: compute needs
      `--glsl "310 es" --hlsl 50 --msl 12` separately; `qsb.bake(..., "comp")` now does this.
    - `QRhiBuffer.Dynamic` + `StorageBuffer` returned false on D3D11 here; `Static` with
      `StorageBuffer | VertexBuffer` works.
    - the compute pipeline needs its own SRB (uniform dynamic offset + storage buffer);
      `beginComputePass(batch)` commits the uniform update before the dispatch.
    - the path vertex pipeline still needs an empty, `create()`d SRB even though it has no
      shader resources.
    - the compute shader is generated per expression (`FI` is expression-specific), so qsb
      is cached by source hash; there is one dispatch per path curve.
    - the old CPU path sampler, per-vertex discs and point-sampled vertex instances are
      gone. QPainterPath (`pathcurve.py`) remains only as a CPU reference prototype.

    Regressions: existing `test_render_smoke.py` tan/log cases now exercise the compute path
    (width, no pole connector, deep log descent), plus `tests/test_pathcurve.py`.

68. **`ggb_reference.py` is the CPU oracle for the interval path, not a renderer.** It ports
    the shape of `FunctionSampler`, `IntervalPath`, `DrawInterval`, `DrawInvertedInterval` and
    `JoinLines` from
    `D:/Projects/geogebra/source/shared/common/src/main/java/org/geogebra/common/euclidian/plot/interval/`.
    The sampler uses `mpmath.iv` for conservative intervals and one interval per screen column;
    the path keeps `last_point`/`last_y` and emits clipped segments. It is used to compare the
    GPU state machine, never imported by `PlotView` or `CurveRhiItem`.

    `mpmath.iv` has no inverse trig/hyperbolic helpers, so the reference currently covers the
    operators the production interval library supports through `sin`, `cos`, `tan`, `sqrt`,
    `exp`, `log`, `Abs`, arithmetic and powers. A missing operator must be added to
    `_iv_module()` before the reference can judge that curve.

    Tests: `tests/test_ggb_reference.py` (tan pole not bridged, reciprocal has both branches,
    log has no path left of its domain). The reference exposed the remaining GPU gap: we still
    need the complete `JoinLines` state, not independent per-column quads.

69. **CPU backend is an explicit fallback, not a second default.** `PlotView.cpuRenderer`
    swaps the `CurveRhiItem` for `CpuCurves.qml`; each curve becomes a `PathCurveItem`
    (`QQuickPaintedItem`) that draws one `QPainterPath` from `ggb_reference.py`. The
    reference uses `mpmath.iv` for conservative one-interval-per-column evaluation and the
    GGB `IntervalPath` state; unsupported interval expressions fall back to the simple
    sampled path in `pathcurve.py`. The CPU layer is lazy: `PlotCurves.qml` keeps it inside an
    inactive `Loader` unless `cpuRenderer` is true.
    This is the fallback for devices where RHI/compute/shaders are unavailable; it is not
    selected automatically yet. A host opts in with `view.cpuRenderer = true` (or
    `widget.rootObject().setProperty("cpuRenderer", True)`). Regression:
    `tests/test_cpu_backend.py`.
