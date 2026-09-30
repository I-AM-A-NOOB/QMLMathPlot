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

## 定义域、极点与描边（Desmos 式）

[Desmos 的工程博客](https://engineering.desmos.com/articles/press-a-key-in-the-calculator/)
写得很清楚：把函数**采样**成点、连成**线段**、按线段描边；"相邻点提示跳变就断开"；
极值/零点再用二分细化。这里照同样思路做：

- **描边 = 到采样折线段的距离**（不是到切线的距离）。切线近似在陡峭/强弯曲处会高估
  距离，笔触会变细、甚至渐变消失（`log(x)` 在 x→0+、`sin(1/x)` 的陡段）；按线段量
  距离则**处处等宽**。
- **跳变断开**：相邻采样点落差超过 4 个视口高就不连线段——`1/x`、`tan(x)` 的渐近线
  处不会画出竖直连线（另外仍保留解析的极点断口，双保险）。
- **定义域**（sympy 推出、注入 `DOM`）：分母 ≠ 0、`log` 实参 > 0、`sqrt` 实参 ≥ 0、
  `asin/acos` 实参 ∈ [−1,1]；域外像素直接丢弃。NaN/inf 采样点既不参与包络也不连线段。
- **欠采样列**（一列里塞进多个振荡）仍整列换成包络带：折线在那种列里是随机锯齿。

实测：`log(x)` 陡段横向宽度恒定 1~2 设备像素（此前是渐细的渐变）；`sin(1/x)` 的细线
宽度均匀；`1/x`、`tan(x)` 极点不连线；`sin(x)`/`x^2`/`exp(-x²)sin(10x)` 不受影响。

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
