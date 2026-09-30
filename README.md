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
- `test_render_smoke.py`：开窗渲染后取像素，验证细线形态与 `sin(1/x)` 的包络带。

## 已知问题

- **奇点附近的包络带高度偏高**：`sin(1/x)` 在 x≈0 的列里一列塞进无穷多个振荡，
  理想画法是填它真实的 ±1 包络；实测（1350×900、默认视图）该列被填满了**整列**。
  原因在 GPU 的超越函数精度：`|1/x|` 很大时着色器里 `sin/cos` 不可靠——实测 8 个
  采样点几乎相同（跨度 ≈1.7 px，CPU 复算是 299 px），个别采样点还是 NaN，于是
  "实测跨度"那条判据失效、包络带权重掉到 ≈0.05，整列由**饱和的描边项**填满
  （`|f'|` 极大时描边公式对任何 y 都算得极小的水平距离）。CPU（numpy）不会遇到
  这个问题，所以离线参考实现是对的。
  可行的方向：把"不可分辨"的判据从"采样跨度"改成以导数为主（`|f'|·dx·sy` 的量级
  在 GPU 上可靠），或对采样点先做参数归约再求值。

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
