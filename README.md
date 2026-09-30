# QMLMathPlot

可嵌入 Qt Quick App 的函数绘图组件。曲线由**片元着色器逐像素**绘制（隐式
signed-distance 模型）：每帧代价 ∝ 像素数、与函数频率无关，振荡函数（如
`sin(1/x)`）不会因为折线混叠把帧率拖垮，也不需要在 CPU 上采样。

```
python main.py --backend d3d11 "sin(1/x)"     # 最小验证窗口（拖拽平移 / 滚轮缩放）
python main.py "exp(-x*x)*sin(10*x)"          # 不指定后端 = Qt 默认后端
```

## 嵌入到 App

```python
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from qmlmathplot import PlotController, qml_component_path, register_qml_types

app = QGuiApplication([])
register_qml_types()                                  # 注册 PlotController 到 QML

controller = PlotController("sin(1/x)")               # ViewModel（App 侧持有）
view = QQuickView()
view.setSource(QUrl.fromLocalFile(qml_component_path()))
view.rootObject().setProperty("controller", controller)
view.show()
app.exec()
```

```qml
import QmlMathPlot 1.0

MathPlot {                       // 组件：Inject ViewModel，或让它自带一个
    anchors.fill: parent
    controller: myPlotController
    lineWidth: 1.5
    curveColor: "#33ccff"
    backgroundColor: "#14141e"
}
```

组件属性：`expression`（sympy 语法，改它会重新生成 GLSL 并烘焙 .qsb）、`view`
（`Qt.vector4d(xmin, xmax, ymin, ymax)`）、`error`（表达式/烘焙失败的原因，成功时为空）、
`lineWidth`、`curveColor`、`backgroundColor`。`controller` 是 ViewModel，暴露
`zoom(delta, u, v)` / `panPixels(dx, dy, w, h)` / `resetView()` 给输入层调用。

## RHI 后端

`--backend {d3d11,d3d12,vulkan,metal,opengl,null}`；**不指定就用 Qt 的默认后端**。
实现是在 `QGuiApplication` 之前设置 `QSG_RHI_BACKEND`（Qt 在平台初始化时读它，
之后再 `setGraphicsApi()` 不生效）。本机（Intel 驱动 32.0.101.8826）实测
`d3d11` 与 `opengl` 输出逐像素一致。

## 结构（MVVM）

| 层 | 文件 | 职责 |
|---|---|---|
| Model | `src/qmlmathplot/model.py` | 表达式 → GLSL、着色器源码模板、`ViewRect` 平移/缩放数学（不依赖 Qt，可单测） |
| ViewModel | `src/qmlmathplot/viewmodel.py` | `PlotController`：expression / view / 着色器 URL / error；`qml_component_path()` 给出组件路径 |
| View | `src/qmlmathplot/qml/MathPlot.qml` | 纯 QML：`ShaderEffect` + 鼠标平移/滚轮缩放 |
| 烘焙 | `src/qmlmathplot/qsb.py` | GLSL → `.qsb`（PySide6 自带 `qsb.exe`），按源码 hash 缓存 |
| 入口 | `main.py` / `src/qmlmathplot/app.py` | 组件封装的最小验证窗口 |

## 算法（视觉上"完美"的两块拼图）

1. **屏幕空间距离描边**：每像素用 `f` 与 `f'` 算它到曲线的一阶距离，
   `cov = clamp(lineWidth/2 + 0.5 − d, 0, 1)`。任何斜率等宽、天然抗锯齿，极点
   （`tan(x)`）和跳变自动正确。
2. **欠采样包络带**：一个像素的 x 区间里塞进多个振荡时（局部周期 < 1 像素），
   逐像素距离已经没有意义（画出来是摩尔纹）。此时把该列的 `[min, max]` 填成
   实心带——对 `sin(1/x)` 就等于填它的真实 ±1 包络。

判据（同一像素内 8 个采样点，取并集）：列内折返 ≥ 2 次；实测跨度远小于导数预期
`|f'|·dx`（深缩放兜底）。两道闸门：跨度 < 1.5 px 不填（别把峰顶起伏填成色块）、
跨度 > 4×视高不填（别把极点涂满整列）。快路径仍只有 1 次 `f` + 1 次 `f'`。

## 测试

```
uv run pytest                      # 全部
uv run pytest -m "not gui"         # 跳过开窗渲染的烟测
QSG_RHI_BACKEND=opengl uv run pytest -m gui
```

- `test_model.py`：GLSL 生成（小整数次幂连乘，避开 `pow(x, y)` 在 x<0 的未定义）、
  着色器注入、视图数学（缩放严格可逆、锚点不漂移）。
- `test_shader_bake.py`：烘出的 `.qsb` 的 stage 必须与用途一致，且覆盖
  GLSL/HLSL/MSL/SPIR-V 四个后端目标。
- `test_render_smoke.py`：开窗渲染后取像素，验证细线形态、`sin(1/x)` 填 ±1 包络、
  中心列外侧不得出现 |y|>1 的伪影、极点不连线、定义域外不画。

## sin(1/x) 奇点附近的三个坑（已修，别再犯）

1. **宏参数必须带括号**。表达式是内联进 `#define F(u) (...)` 的，宏是**文本替换**：
   `F(x - h)` 遇上 `SIN(1.0/u)` 会展开成 `SIN(1.0/x - h)`，于是 8 个采样点全算的是
   `1.0/x - h_k`（只差个微小偏移）——采样永远几乎相同，`turns=0`、`spread≈0`、包络
   权重≈0，**包络带从来没生效过**。现在 `func_glsl(..., wrap=True)` 把参数印成 `(u)`，
   `test_model.py` 里有回归。
2. **GPU 的 sin/cos 在大参数上不可靠**。`sin(1/x)` 在奇点附近实参能到几百上千；
   实测（Intel D3D11 与 OpenGL 一致）`cos(1/x)` 返回近 0 的垃圾、`sin` 的采样点几乎
   相同。现在生成表达式时把 `sin/cos` 包成 `SIN/COS`：先把实参折进 `[0, 2π)` 再调
   内置函数（小参数各家实现都准）。实测 `d1` 从垃圾的 ~200 回到正确的 ~-18200。
3. **描边在陡峭列会饱和**。`|f'|` 极大时切线近似对任何 y 都算得极小的水平距离，
   `cov` 会饱和成整列，还会沿切线外推到远超真实值域的地方（实测把 `sin(1/x)` 涂到
   ±1.8，而它的值域是 ±1）。现在触发列里先用**采样包络**钳住描边，再用包络带整列替换。
4. **逐列采样对振荡函数就是相位噪声**。某一列的采样点碰巧漏掉极值/挤在一起时，
   判据会误判"可分辨"或包络偏窄——表现为虚实交替的梳状锯齿与带边缘的台阶
   （实测相邻列上边缘平均差 **93.6px**、最大 421px）。现在用两把尺子分开：
   - **窄窗口（±2 列、8 点）**判"这一列画不下"→ 决定填哪些列（窗口窄，填充范围不会被撑宽）；
   - **宽窗口（±8 列、16 点）**只用来估带的上/下边缘 → 采样点更可能碰到极值，边缘才平。
   实测带内相邻列上边缘差降到 **2.4px**；实心带宽度 20 逻辑列（与 numpy 参考版的
   20/900 一致）。这里是有意取舍：**宁可多填一环带，也不画出锯齿**。

修好后（1350×900、默认视图、D3D11 与 OpenGL 逐像素一致）：`sin(1/x)` 奇点列点亮
比例 0.50（= 填 ±1 包络的半列）且边缘平齐，中心 41 列里落在 |y|>1 之外没有实心像素
（修前是 7961 个）；`sin(x)`/`tan(x)`/`x^2`/`exp(-x²)sin(10x)` 的点亮范围都符合预期。

## 定义域与极点

从表达式结构里推出两件事，注入着色器（`pole_glsl()` / `domain_glsl()`）：

- **极点因子**：会**变号**的分母（`x`、`cos(x)`…）。在采样窗口两端求值，乘积 ≤ 0 说明
  该区间跨过极点（+∞ 跳到 −∞）——这种列**留断口**，不画竖直连线。`sin(1/x)` 这类
  **有界**振荡不在此列（判据同时要求采样跨度 > 32 倍视口高度）。`1/x²` 这种不变号的
  极点也不需要断口（两侧都趋向 +∞，本来就自然相连）。
- **定义域条件**：分母 ≠ 0、`log` 实参 > 0、`sqrt` 实参 ≥ 0、`asin/acos` 实参 ∈ [−1,1]、
  `tan/sec` 的 cos ≠ 0… 域外像素直接丢弃。
- 采样点里的 **NaN/inf 不参与**包络与折返统计：以前把 NaN 当 0，会把包络一路抬到 0，
  `log(x)` 在 x→0+ 因此被一整块填充盖住（看着像"曲线消失"）。

实测：`1/x`、`tan(x)` 极点列点亮 **0** 像素（此前整列 900，即"渐近线连线"）；
`log(x)`/`sqrt(x)` 在 x<0、`asin(x)` 在 |x|>1 全为 0；`sin(1/x)` 的 ±1 包络带不受影响
（454 像素/列，与改动前一致）。

## 踩过的坑（别再犯）

- **qsb 靠源文件后缀判 stage**：写成 `.glsl` 会被当成 vertex，片元着色器就会
  被烘成顶点着色器 → D3D11 的像素阶段拿到非法 HLSL（`X4502`），Intel 驱动直接
  第一帧挂死；GL 后端因为按槽位赋 stage 而看不出来。见 `qsb.bake()` 与
  `test_shader_bake.py`。
- **取像素用 `QQuickItem.grabToImage()`（异步）**，不要用
  `QQuickWindow.grabWindow()`：后者的同步握手会和 Python 侧对象死锁，表现为
  窗口"未响应"。
- 表达式用**宏**而不是 GLSL 用户函数：一个像素里要对同一个 x 求值 9 次，
  宏是预处理展开、没有函数调用语义。
