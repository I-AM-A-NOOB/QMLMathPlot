# 设计目标（必须坚持）

> 你正在 qmlmathplot 的上游源码仓库工作，目标版本为 0.7。
> 0.6.0 已经发布，不要重写 tag；0.6.1 只允许修 bug，结构性收敛放 0.7。

1. 核心（Program/Plot/Curve/PlotView）不感知进程，不包含 subprocess/ProcessCalculator。
2. plt 风格 API 保持串行：
   - Plot.add_curve("sin(x)") 在配置了同步 ProgramSource 时，同步得到 Program。
   - Plot.add_points(x, y) 永远同步、纯数据。
   - Plot.add_program(program) / Curve.set_program(program) 是解耦前端的入口，
     不解析、不 import SymPy、不感知进程。
   - 异步只属于前端：worker 返回 Program 后由前端调用 add_program/set_program。
3. SymPy 是可选的解析前端（[calc] extra）；核心 import、Plot/Curve/PlotView
   import 均不得加载 SymPy。没有 [calc] 时，文本 add_curve 给明确错误，
   但 add_program 仍可用。
4. Program 是唯一边界：kind（intervals/adaptive/implicit/data）、glsl、
   math/numpy source、error；纯数据，可跨进程/JSON/pickle。
5. CPU fallback 来自 Program.math/numpy source，核心编译后使用；不要求 SymPy。
   implicit 的 CPU fallback 要么实现轮廓，要么明确报 unsupported，不能静默空白。
6. qsb 首次和后续 bake 都异步：先占位或保留上一张图，generation/token 丢弃过期结果；
   错误进 curve.error/plotError，绝不抛回 QML，不阻塞结果展示。
7. ProcessCalculator 移到 examples/ 或独立可选前端包；核心不提供默认进程策略。
   默认 Plot 不应悄悄创建 InlineCalculator；需要显式注入 ProgramSource，
   或仅在 [calc] extra 中提供默认同步 SymPy source。

## 需要核实的 0.6.0 问题

逐项对照源码确认/修复：

1. Curve.__init__ 仍 _set_expression(..., synchronous=True)；ProcessCalculator
   回答后 _on_program 仍可能在 UI 线程同步 _run_bake。README 却称 asynchronous。
2. program.compile_function 的 exec namespace 没有显式 __builtins__={}，
   注释“no builtins”不成立；这是信任边界，必须修/明确 trusted calculator 契约。
3. mpl.py 顶层 import sympy，to_matplotlib 依赖 curve.expr；matplotlib extra
   又不带 SymPy/calc，单独安装会失败。改成 Program.numpy/math 采样，或让
   matplotlib extra 包含 calc。
4. CpuCurves.qml 把 implicit 也交给 PathCurveItem，而 PathCurveItem 只支持 1D；
   implicit CPU 没有明确实现。
5. renderMode "auto" 仍走 RHI；真正的 QPainter CPU 路径只有显式 "cpu"。
   文档声称 RHI unavailable 时 CPU；修改语义或文档，并测试。
6. 核心仍有 ProcessCalculator/subprocess；Plot() 默认 InlineCalculator；
   Curve.expr deprecated 但仍 import SymPy。按目标 1/2/3 收敛。
7. __init__._LAZY 缩进不齐、ruff 报 I001 等格式/import 问题；发布前跑 lint。
8. 文档与代码矛盾：async 首帧、CPU 自动回退、matplotlib extra、SymPy 依赖。
   README/API docs 必须和实现一致。

## 宿主参考：Symplify（可选，按需）

Symplify 是本库的下游宿主，路径 D:\Projects\Symplify。
不要递归 read/grep/find 这个仓库；它有大量未提交改动和 2900 行 guide，会塞满上下文。
默认使用下面摘要，不打开它。只有需要核对某个具体契约时，才用 read 的窄行范围打开：

- python/viewmodel/calculator_viewmodel.py（只看 plot 相关：_get_plot/_set_plot/_apply_plot）
- python/model/plotting.py（plot_series 的 explicit/implicit 决策）
- qml/components/PlotPane.qml 或 qml/pages/CalculatorPage.qml（PlotView 注入）
- main.py（qmlmathplot 注册）
- docs/architecture.md 的 1.1/1.2（中文图，个人文档）
- .Agent/DEVELOPER_GUIDE.md 只读 qmlmathplot 相关小节，不要读全文。
禁止对整个 Symplify 仓库做 grep/find/ls 递归。

Symplify 集成摘要：

- PySide6/QML 桌面计算器；CalculatorViewModel 持有一个 qmlmathplot.Plot，
  QML 通过 PlotView { plot: calcVM.plot } 显示；theme/legend/aspect=1 已设置；
  result/variants 更新时调用 add_curve/add_implicit；错误显示在 plot pane；
  plot 属于 viewmodel，页面重建后仍存活。
- 当前依赖 qmlmathplot>=0.5,<0.6，UI 进程 import SymPy；
  Calculator 有独立 worker；目标是 worker 返回 Program，UI add_program，
  不再 import qmlmathplot.calc/SymPy，也不新增进程。
- 现有 plot 决策在 python/model/plotting.py（explicit/implicit/origin）；
  迁移时这部分由 worker 产出 Program 或 PlotSpec，UI 只消费数据。
- QML 2.0 迁移尚未做；不要在本任务中修改 Symplify。

## 任务

1. 先读 qmlmathplot 源码，给出 0.7 的最小收敛设计（模块/API/兼容层），
   指出与上述目标的冲突；不要直接大改。
2. 实现核心收敛：
   - 去掉 core 进程；ProcessCalculator 移到 examples/optional extra。
   - 同步 ProgramSource 协议 + add_program/set_program。
   - [calc] 提供 SymPy source；base import 不加载 SymPy。
   - CPU fallback/隐式边界、Program.compile builtins、异步首帧 bake。
   - mpl/export 与 extras 一致。
   - 修正文档、格式、lint。
3. 保留 0.6 兼容层（deprecation），不要无提示删除公共 API。
4. 测试：
   - 屏蔽 SymPy 后 import qmlmathplot / Plot / PlotView 成功；
   - add_program 不 import SymPy；add_curve 无 [calc] 明确报错；
   - 同步 ProgramSource + 串行 add_curve；add_points；
   - 首帧 qsb 异步（不阻塞）、stale generation、错误路径；
   - CPU implicit 行为明确；matplotlib extra 在无 SymPy 情况下要么可用要么报清晰错误；
   - ruff/既有测试。
5. 输出：设计说明 + 改动文件 + 测试结果 + 迁移/兼容说明。
   不要修改 D:\Projects\Symplify；不要新增常驻进程到核心。

## 约束

- 不用“再加一个 adapter 层”掩盖职责；核心只保留 Program/Plot/Curve/渲染。
- 不在核心 import SymPy；不在核心管理进程；不在 UI 线程执行 qsb 首帧。
- 不把 qmlmathplot.calc 的 SymPy source 直接 exec 到有 builtins 的 namespace。
- 先报告发现再实施结构性改动；不要在没有测试的情况下重写 0.6 API。
