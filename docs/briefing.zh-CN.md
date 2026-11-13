# QMLMathPlot 维护者简报（中文）

> 面向作者自己的内部文档，不是用户手册。用户文档见 `docs/index.md`；工程陷阱的原始记录见
> `AGENTS.md`。版本基线：0.7.0（unreleased）。

## 0. 一分钟版本

- 核心只消费 **`Program`**：不解析文本、不 import SymPy、不管理进程。文本由显式注入的
  同步 `ProgramSource` 变成 `Program`（`[calc]` 里的 `SymPyProgramSource` 是内置实现）。
- 文本曲线：`Program` 同步返回，**qsb 着色器异步烘焙**（线程池）；失败保留上一张图，
  错误进 `curve.error` / `plot.plotError`，不抛进 QML。
- 四条渲染路径：
  1. `intervals` → GPU compute（GGB interval path 状态机，一列一次 `FI`）；
  2. `implicit` → GPU fragment（逐像素 signed distance + 域/极点守卫 + marching cell）；
  3. `adaptive` → CPU `CurvePlotter` 生成世界路径 → 共享 `path.vert/frag` 笔画；
  4. `data` → 点集折线走同一笔画管线。
- `renderMode="gpu"`（RHI）或 `"cpu"`（QPainter，给无 RHI 平台；软件场景图
  `QT_QUICK_BACKEND=software`）。CPU 模式目前只完整覆盖 1-D；implicit 明确提示，
  data / numpy-only 源尚未绘制。
- 测试基线：277 passed、3 xfailed（oracle 已知差异）；`ruff`（F/E9/I）全绿。

## 1. 文件逐个作用

### 1.1 仓库顶层

| 路径 | 作用 |
|---|---|
| `src/qmlmathplot/` | 库源码（见下） |
| `tests/` | pytest 套件；GUI 测试标记 `gui` |
| `examples/` | 最小窗口、Quick explorer、进程 worker、QtWidgets 示例 |
| `docs/` | 用户文档 + 本简报 + 历史设计文档 |
| `tools/ggb_oracle/` | 用真实 GeoGebra 生成 oracle fixture 的 Java 探针 |
| `AGENTS.md` | 历史踩坑记录（编号条目，改渲染前先读） |
| `CHANGELOG.md` | 版本变更（0.7.0 目前 unreleased） |
| `pyproject.toml` | 版本、依赖、extras、pytest/ruff 配置 |
| `uv.lock` | uv 锁文件（镜像 URL 会随本机 uv 配置变化） |

### 1.2 `src/qmlmathplot/` 核心

| 文件 | 作用 |
|---|---|
| `protocol.py` | `Program`、`ProgramSource` 协议、`ProgramSourceMissingError`、帧编解码（无 Qt/SymPy） |
| `program.py` | 把 `Program.math/numpy` 编译成函数；命名空间只含模块 + `abs` + 显式空 `__builtins__` |
| `plot.py` | `Plot`：相机、曲线列表、标注/参考线、样式、主题、导出入口、`renderMode`、`plotError` |
| `camera.py` | `Camera`：centre/zoom/aspect、派生范围、nice ticks、输入与动画、`set_viewport` |
| `curve.py` | `Curve`/`CurveListModel`/`ImplicitCurve`：文本 → `Program` → 异步 qsb、generation、错误保留；数据点 |
| `view.py` | QML 类型注册（URI `QmlMathPlot 2.0`）与组件路径 |
| `rhi.py` | `CurveRhiItem`：一个 `QQuickRhiItem` 画全部曲线；每 program 一个 pipeline；snapshot、动态 offset uniform |
| `curveplotter.py` | GGB `CurvePlotter`/`CurveSegmentPlotter` 的 Python 移植：bisection stack、SegmentParams、连续性/定义域边界、输出 `MOVE_TO/LINE_TO` |
| `pathcurve.py` | CPU 模式：把 `Program.math` 的 ops 画成 `QPainterPath`；旧裸表达式兼容路径 |
| `export.py` | 离屏 `QQuickWidget` 渲染（惰性导入 QtWidgets）；导出像素尺寸与相机范围 |
| `mpl.py` | 可选的 matplotlib 交接：采样 `Program.numpy`，隐函数跳过；不依赖 SymPy |
| `widget.py` | `MathPlotWidget`：`QQuickWidget` 桥接 + 单曲线便利 API + `render_mode` |
| `app.py` | 命令行窗口；`--backend` / `--render-mode` / `--software` |
| `input.py` | `ViewInput`：键盘开关、步长、可重绑定快捷键 |
| `overlay.py` | `Annotation`、`Guide` 与两个 list model |
| `animation.py` | `QAbstractAnimation` 驱动的相机缓动（pan/zoom 共用） |
| `themes.py` | 读取 `themes/*.mplstyle`，映射到 `Plot` 属性 |
| `qsb.py` | 调 `qsb.exe` 烘焙 `.qsb`；按源码哈希缓存；Windows 隐藏控制台；缓存清理 |
| `shaderbuild.py` | 读 `shaders/`、展开 `@INCLUDE:common/…@`、替换占位符、校验残留 |
| `ggb_path.py` | 纯 Python 的 GGB interval path 状态机规范（`IVal`/`Column`/`PathPoint`/`IntervalPath`）；compute shader 镜像它 |
| `__init__.py` | 惰性导出：`import qmlmathplot` 不加载 Qt/SymPy |
| `__main__.py` | `python -m qmlmathplot` → `app.main` |

### 1.3 `src/qmlmathplot/qml/`

| 文件 | 作用 |
|---|---|
| `PlotView.qml` | 组件根：模型注入、尺寸上报、相机映射、键盘/焦点；`renderMode` 跟随 `plot.renderMode` |
| `PlotCurves.qml` | GPU：RHI item 放在 `Loader` 里（CPU 模式不实例化）；CPU：`CpuCurves` 放在另一个 `Loader` |
| `CpuCurves.qml` | 每条曲线一个 `PathCurveItem`；隐函数显示 “implicit curves need the GPU renderer” |
| `PlotFurniture.qml` | Canvas 画的网格/坐标轴/刻度/标题/标签（像素对齐见 AGENTS 35/36） |
| `PlotInput.qml` | 鼠标/滚轮/触摸事件 → 相机；不抢焦点/不泄漏给父级 |
| `PlotOverlay.qml` | 标注、参考线 |
| `PlotLegend.qml` | 图例 |

### 1.4 `src/qmlmathplot/shaders/`

| 文件 | 作用 |
|---|---|
| `fullscreen.vert` | RHI 全屏三角形顶点着色器 |
| `implicit.frag` | 隐函数：`F`、`FX`、`FY`、`DOM`、`POLE` 宏；域外延拓、Newton 投影证书、极点守卫、四角 marching cell、singular disc |
| `interval_lib.glsl` | 区间运算库（`IVal` 拓扑 EMPTY/CONNECTED/INVERTED/WHOLE、算术、初等函数） |
| `interval_path.comp` | interval path 的 compute 着色器模板：一次调用逐列 `FI` + 状态机 → storage buffer |
| `path_state.glsl` | 状态机的 GLSL 镜像（break/join、inverted、whole、join lines） |
| `path.vert` / `path.frag` | 共享笔画管线：屏幕空间四边形 + `local` 圆盘做圆角接头；compute 与 CPU path 共用 |
| `common/uniforms.glsl` | uniform block 布局（与 Python 端 128 字节一致） |
| `common/arg_reduction.glsl` | `SIN/COS` 的大参数归约（GPU 内建函数不可靠） |
| `common/real_pow.glsl` | 负数奇次根 `_real_pow` |
| `common/seg_dist.glsl` | 点到线段距离 |
| `common/span_cov.glsl` | 覆盖/带宽工具 |

### 1.5 `src/qmlmathplot/calc/`（唯一 import SymPy 的包）

| 文件 | 作用 |
|---|---|
| `__init__.py` | `SymPyProgramSource`（同步 text→Program）、导出 `build_program` |
| `build.py` | 路由：Eq/自由 y → implicit；interval 资格 → intervals；否则 adaptive；生成 GLSL + `math`/`numpy` 源码 |
| `model.py` | 表达式分析/打印：GLSL printer（小整数幂展开）、`func_glsl`、`implicit_shader_sources`、domain/pole 分析 |
| `interval.py` | 生成 `IVal FI(xlo,xhi)`；`interval_lib_for` 按依赖裁剪库；`compute_supported` = GGB eligibility + 本库能生成 `FI` |
| `ggb_compute.py` | 生成 `interval_path.comp` 的完整源；`path_subsamples`（当前 1，一列一个区间） |
| `ggb_eligibility.py` | GGB `IntervalFunctionSupport` 的移植：单次 x 出现 + 算子白名单 |
| `ggb_reference.py` | 测试 oracle：`mpmath.iv` 列采样 + `ggb_path` 状态机 |
| `interval_reference.py` | 测试 oracle：`interval_lib.glsl` 语义的 Python 镜像（与 GGB fixture 对比） |
| `__main__.py` | worker：stdin/stdout 帧协议，`build_program` 后返回 |

### 1.6 `tests/`（按用途分组）

| 组 | 文件 |
|---|---|
| 契约/异步 | `test_protocol.py`、`test_program_source.py`、`test_calc_build.py`、`test_calc_worker.py`、`test_async_bake.py` |
| 无 SymPy 隔离 | `test_no_sympy_ui.py`、`test_no_sympy_blocked.py` |
| 模型/样式 | `test_plot.py`、`test_curve.py`、`test_themes.py`、`test_points.py` |
| GGB oracle | `test_ggb_oracle.py`、`test_ggb_path.py`、`test_ggb_path_oracle.py`、`test_ggb_reference.py`、`test_interval_reference.py`、`test_ggb_curve_plotter*.py`、`test_model.py` |
| 渲染 | `test_render_smoke.py`、`test_implicit.py`、`test_rhi.py`、`test_pathcurve.py`、`test_shader_build.py`、`test_shader_bake.py`、`test_qsb_cache.py` |
| Qt 集成 | `test_widget.py`、`test_qtquick_coexistence.py`、`test_cpu_backend.py`、`test_input.py`、`test_overlay.py`、`test_export.py` |
| 交接/CLI | `test_handoff.py`、`test_cli.py` |

`tests/conftest.py` 做两件事：注入 `SymPyProgramSource`（生产 API 仍然显式；需要测“缺 source”
时显式传 `program_source=None`），以及把 `Curve.bake_async` 设为 False 让测试确定同步。
`tests/fixtures/ggb_curve_plotter/` 是真实 GGB 的路径 fixture。

### 1.7 其它

- `examples/explorer_qtquick.py` + `.qml`：Quick 共存探针（详见 §2）。
- `examples/process_calculator.py`：宿主侧 worker 往返 + `--check-isolation`。
- `examples/widget_minimal.py`：QtWidgets 最小用法。
- `examples/minimal.py`：走 `qmlmathplot.app` 的 CLI 窗口。
- `themes/*.mplstyle`：从 matplotlib 风格表复制/裁剪；`themes.py` 解析。

## 2. 接口与架构

### 2.1 没有 plt 风格 API 了

0.6 已删除 `Figure`/`Axes`/`FigureCanvasQt`/`qmlmathplot.pyplot`，0.7 删除 `to_sympy`。
现在的接口只有：

1. **Python 模型**：`Plot` / `Camera` / `Curve`，可配任意 Qt 视图或离屏；
2. **Qt Quick**：`PlotView { plot: backendPlot }`，模型必须由 Python 注入；
3. **QtWidgets**：`MathPlotWidget(..., program_source=..., render_mode=...)`；
4. **CLI**：`qmlmathplot` / `python -m qmlmathplot`；
5. **离屏/交接**：`Plot.to_image()`、`savefig()`、`to_matplotlib()`。

历史设计（Figure/Axes 的取舍）留在 `docs/api-design.md`，仅作背景。

### 2.2 三个示例的分工

- `explorer_qtquick.py`：**共存**。ToolBar（输入/示例/纵横比/主题）、SplitView（绘图 + 日志）、
  半透明 overlay、无 Tab 焦点抢占。它验证的是事件/焦点/尺寸，不是渲染质量。模型由
  `engine.rootContext().setContextProperty("backendPlot", Plot(...))` 注入；QML 不自行建曲线。
- `process_calculator.py`：**进程边界**。同步请求 `python -m qmlmathplot.calc` → `Program`
  → `plot.add_program`；`--check-isolation` 断言 UI 进程没有 SymPy。
- `widget_minimal.py`：QtWidgets + `MathPlotWidget`，展示单曲线便利 API。

### 2.3 控制流（文本曲线）

```
Plot.add_curve(text)
  -> Curve._set_expression: 记录文本、generation+1
  -> ProgramSource.program(text, implicit=...): 同步返回 Program
  -> _apply_program: Program 立即挂到 curve.program
     -> 有 GLSL: QThreadPool(_BakeJob) -> qsb -> (generation 匹配) 更新 QUrl
     -> 无 GLSL: 直接 finish
  -> CurveRhiItem._refresh (GUI 线程) 快照: (url, 样式, kind, compute_url, cpu_vertices)
  -> render 线程: pipeline 复用/创建 + draw
```

错误：`Program.error` → `curve.error`；bake 异常 → `curve.error`，旧 shader 保留；
source 缺失 → `ProgramSourceMissingError`（Python 抛，QML 不调用 add_curve）。

### 2.4 线程/进程职责

- GUI 线程：模型、同步 source、QML；CPU 模式还负责 QPainter 绘制。
- `QThreadPool`：`qsb`（子进程等待，不吃 GIL）。
- 渲染线程：从已烘焙 `.qsb` 建 pipeline、资源上传、draw；不解析、不跑 qsb。
- 计算进程（可选、宿主自有）：SymPy/任意语言；核心不启动、不重启。

## 3. 仿 GGB：表达式分类与实现

### 3.1 分类决策（`calc/build.py`）

```
Eq(lhs, rhs) / 显式 implicit=True / 自由符号含 y  -> implicit
否则若 ggb_eligibility.supported(expr)             -> intervals
否则                                               -> adaptive
点集（add_points）                                 -> data
```

`ggb_eligibility.supported` = GGB `IntervalFunctionSupport`：

- `x` 的出现次数 ≤ 1（多出现如 `x*sin(x)`、`x/(1+x**2)` 直接出局）；
- 算子白名单：`sin cos tan asin acos atan sinh cosh tanh exp log Abs cot sec csc`、
  四则、数值幂、`E**f`；`floor`/`sign`/`Min`/`Max`/`Piecewise` 不在内；
- 依赖商（如 `x/sqrt(x**2)`）：`x` 出现两次（GGB 还会化简成 `sgn`），由变量计数门拒绝；
  没有单独的“依赖商识别”函数。

### 3.2 `intervals`（GPU compute，GGB interval path）

- `interval.py` 生成 `IVal FI(float xlo, float xhi)`：`IVal` 是 `(kind, lo, hi)` 的区间集合，
  拓扑 EMPTY / CONNECTED / INVERTED / WHOLE；`interval_lib_for` 只嵌入表达式用到的库函数闭包。
- `ggb_compute.path_compute_source` 生成 `interval_path.comp`：**一次 invocation 按屏幕列顺序**
  循环（不是每列一个线程），每列一次 `FI` + 一列预取；`ggb_path.IntervalPath` 的状态机
  在 `path_state.glsl` 中镜像：break/join、joined/independent/whole、inverted gap rays、
  `JoinLines` 的邻居补线。
- `path_subsamples()` 目前恒为 1：GGB `FunctionSampler` 一列一个区间。
- 输出的段写入 storage buffer，由 `path.vert/path.frag` 画；与 CPU path 共用笔画管线。

### 3.3 `adaptive`（CPU CurvePlotter，未过门 1-D）

- `curveplotter.py` 是 GGB `CurvePlotter`/`CurveSegmentPlotter` 的移植：二分栈
  (`CurvePlotterStack`/`StackItem`)、`SegmentParams` 的距离/角度阈值、连续性检查、
  定义域边界搜索、问题区间递归；输出世界坐标 `MOVE_TO`/`LINE_TO`/`END`。
- `rhi.py` 在 GUI 线程用 `plot_curve(Program.math)` 生成 ops，`stroke_vertices` 变成屏幕四边形，
  RHI 的 `path` pipeline 画；CPU 模式则由 `pathcurve.py` 画成 `QPainterPath`。
- 覆盖 GGB 的 `CurvePlotter` 语义（含 `floor`/`sign`/`Min`/`Max`/依赖商），
  fixture 在 `tests/fixtures/ggb_curve_plotter/`，逐操作 1e-8 对齐。

### 3.4 `implicit`（GPU fragment）

不是 GGB 的 quadtree/Bernstein，而是逐像素 signed-distance：

- `|F| / |∇F|` 给一阶距离；定义域外像素用 8 方向 × 3 半径的域内样本做 Newton 投影，
  边界带两侧都能画；极点因子 `POLE` 跨零点拒绝；收敛残差证书拒绝伪零。
- 四角 marching cell 覆盖中心投影失败的细/切向分支；`|Fx|+|Fy|<1e-3`（世界单位）+
  `|F| < R|∇F|` 的 singular cell 画孤立点。
- 与 GGB 隐函数（多项式 quadtree）不是同一算法，只借了 cell/singular 思想。

### 3.5 oracles 与已知差异

- `calc/ggb_reference.py`：`mpmath.iv` 逐列区间 + 共用状态机；mpmath 无法表达 gap
  （极点列报 empty，路径断开），因此不是 GPU 的像素级 oracle。
- `calc/interval_reference.py`：`interval_lib.glsl` 的 Python 镜像，用于和 GGB fixture 比拓扑。
  `tests/test_interval_reference.py` 的 3 个 xfail 是已知差异：
  `cot(sec(x))`、`ln(cosh(x))`、`sqrt(tan(cot(x)))`（`IntervalDivide`/log 边界/overflow 语义）。
- `tools/ggb_oracle` 是真实 GGB 的 Java 探针，生成 fixture；不要提交对 GGB 源码的修改。

### 3.6 例子对照

| 表达式 | 路由 | 原因 |
|---|---|---|
| `sin(x)`、`tan(2*x)`、`1/x`、`log(x)` | intervals | 单次 x + 白名单算子 |
| `x*sin(x)`、`x/(1+x**2)` | adaptive | x 出现两次 |
| `x/sqrt(x**2)` | adaptive | GGB 化简为 sgn（依赖商） |
| `floor(x)`、`sign(x)`、`Min/Max` | adaptive | 不在 GGB interval 算子表 |
| `x**2 + y**2 - 1`、`Eq(x, log(y))` | implicit | 自由 y / Eq |
| `add_points(x, y)` | data | 纯数据 |
| `exp(tan(x))` | intervals | 资格通过；极点列由 POLE/状态机处理（AGENTS 60/71） |

## 4. RHI 与 CPU 渲染

### 4.1 RHI（默认）

- 一个 `CurveRhiItem`，每个 program 一个 fragment/compute pipeline；所有曲线一个 pass。
- 每曲线 128 字节 uniform，打包进一个动态 offset buffer（AGENTS 65）；只上传变化的行。
- `snapshot` 在 GUI 线程生成，render 线程只读；CPU path 顶点也随快照传。
- 资源更新 batch：每帧 `nextResourceUpdateBatch`，`endPass` 后 `release`；不要跨帧持有
  （AGENTS 64）。
- 首帧 compute pipeline `create()` 0.3–1.2 s（QRhi 限制）；qsb 已在线程池预烘焙。

### 4.2 CPU（平台模式）

- `Plot.renderMode="cpu"`；`PlotCurves.qml` 的 RHI `Loader` 不激活，所以软件场景图下不会出现
  “No QRhi found” 警告。
- 1-D：`PathCurveItem` → `curveplotter.plot_curve(Program.math)` → QPainterPath；
  implicit 显示明确提示；data / 仅 numpy 源目前空白（待补）。
- 软件后端：`QT_QUICK_BACKEND=software` 必须在 `QGuiApplication` 之前；`qmlmathplot --software`
  已封装。
- 性能：单曲线一次相机变化约 1–7 ms（GUI 线程），多曲线 pan 是主要瓶颈。

### 4.3 导出

`to_image`/`savefig(backend="qt")` 用离屏 `QQuickWidget`（惰性 QtWidgets），继承
`plot.renderMode`；`savefig(backend="matplotlib")` 走 `mpl.py` 的采样图（无 SymPy）。
导出前要排空 `Canvas` 的排队绘制（AGENTS 54）。

## 5. 性能数据（本机实测）

| 项 | 数值 |
|---|---|
| `grabToImage` 900×600（含回读） | `sin(1/x)` ≈ 13–14 ms，`tan(x)` ≈ 12 ms |
| compute pipeline `create()` | 0.30 s（sin）/ 1.21 s（tan）每次 widget |
| qsb 冷/热 | ≈ 73–126 ms / 0.15 ms（子进程，不吃 GIL） |
| `curveplotter` 一次路径 | ≈ 1–7 ms（floor/sign 最慢；去 lambdify 后） |
| `Program` 源码生成 | 0.5–1 ms/表达式 |
| CPU 模式 pan | 每曲线一次相机变化 ≈ 1–7 ms（GUI 线程） |

## 6. 开发与测试流程

```bash
uv run pytest -p no:faulthandler          # 本机必需；否则 GUI 套件可能挂起
uv run pytest -m "not gui" -q             # 快速子集（CI 用）
uv run ruff check src tests examples       # F/E9/I
```

- GUI 测试约 107 s（每个测试新建 QRhi，首帧 ~1.2 s），不要过早判卡死；强杀会留下
  python/uv 孤儿进程，抢窗口激活（AGENTS 43/73）。
- `tests/conftest.py` 注入 SymPy source 并关闭异步 bake；测试“缺 source”时显式传 None。
- 3 个 xfail 是 oracle 已知差异，`strict=False`。
- 改 shader 后注意 qsb 缓存按源哈希，旧 `.qsb` 不会复用；`tests/test_qsb_cache.py` 覆盖缓存 API。
- `.pytest_cache` 在本机是旧账户 ACL 残留，只影响缓存警告。

## 7. 发布与打包

- 版本在 `pyproject.toml`；0.7.0 目前 unreleased。
- extras：`[calc]`=sympy；`[matplotlib]`/`[mpl]`=matplotlib。核心依赖 PySide6 + numpy。
- 发布：填 CHANGELOG 日期 → push → `git tag v0.7.0` → push tag；GitHub Actions
  `Publish to PyPI` 构建并在 tag 时发布。本机 `github.com:443` 不通，`origin` 已改为
  `ssh://git@ssh.github.com:443/...`（deploy key；不想保留时 `gh repo deploy-key delete`）。

## 8. 已知限制 / 路线

- CPU 模式：data 曲线、仅 numpy 源、implicit 轮廓未做；implicit 目前明确提示。
- 运行时 GPU→CPU 自动 fallback 未做；`renderMode` 是启动时的平台选择。
- compute pipeline 创建仍在 render 线程。
- 大数据 `add_points` 每次相机变化重建屏幕顶点；可改为世界坐标一次上传 + 顶点着色器变换。
- 1-D compute 路径是单 invocation 顺序列循环（不是并行）；这是 GGB 状态机的形状。
- matplotlib 交接跳过 implicit；导出需要 QtWidgets/QApplication。
- 3 个 interval oracle xfail（非渲染问题）。

## 9. 必须记住的坑（精选，详见 AGENTS）

1. 宏参数必须加括号（表达式是文本替换）——AGENTS 1。
2. qsb 按文件后缀判 stage，源文件必须 `.vert/.frag/.comp`——AGENTS 3。
3. `QSG_RHI_BACKEND` 必须在 `QGuiApplication` 之前设置——AGENTS 16。
4. `QQuickItem.grabToImage` 是异步的；不要用同步的 `QQuickWindow.grabWindow`——AGENTS 15/26。
5. Python tuple 进 QVariant 会失效；tick/结构体用 list——AGENTS 29。
6. Qt `Property(notify="name")` 传字符串会静默丢 notify；传 Signal 对象——AGENTS 30。
7. 自定义 `QAbstractListModel` 不能作为 Property 类型；声明 `QObject`——AGENTS 31。
8. 父组件 `Component.onCompleted` 早于子组件；不要绑定默认子对象——AGENTS 32。
9. `QQuickWidget` 的离屏导出要排空 Canvas paint；`setProperty` 要设在 `rootObject()`——AGENTS 37/54。
10. qsb 子进程在无控制台宿主里要 `CREATE_NO_WINDOW`——AGENTS 57。
11. RHI render 回调是 Python，需要 GIL；不要在 GUI 线程阻塞等首帧——AGENTS 64。
12. `Program.compile` 的 namespace 必须显式 `__builtins__ = {}`——0.7 信任边界。
13. CPU 模式下 RHI item 必须用 `Loader` 停用；仅 `visible:false` 仍会警告/初始化——AGENTS 80。

## 10. 改哪里看哪里

| 想改 | 看 |
|---|---|
| 表达式路由/资格 | `calc/build.py`、`calc/ggb_eligibility.py` |
| interval 语义/GLSL | `calc/interval.py`、`shaders/interval_lib.glsl` |
| compute path 状态机 | `ggb_path.py`、`shaders/path_state.glsl`、`calc/ggb_compute.py` |
| 隐函数画质 | `shaders/implicit.frag`、`calc/model.py` |
| 未过门 1-D 路径 | `curveplotter.py`、`rhi.py::_cpu_vertices` |
| 笔画宽度/接头 | `path.vert/frag`、`curveplotter.stroke_vertices` |
| RHI 资源/管线 | `rhi.py` |
| CPU 模式 | `qml/PlotCurves.qml`、`qml/CpuCurves.qml`、`pathcurve.py` |
| QML 家具/交互 | `qml/PlotFurniture.qml`、`PlotInput.qml`、`PlotOverlay.qml` |
| 模型/属性/主题 | `plot.py`、`camera.py`、`curve.py`、`themes.py` |
| 导出/交接 | `export.py`、`mpl.py` |
| 计算器/worker | `calc/__init__.py`、`calc/__main__.py`、`examples/process_calculator.py` |
