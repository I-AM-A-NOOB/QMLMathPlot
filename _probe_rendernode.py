"""验证 QSGRenderNode 能否从 Python 直接用 raw GL 画曲线（完全不碰 .qsb / qsb 子进程）。

着色器复用 QWidget 前端那一套（core.widget_shader_sources）：如果这条路通，
"运行时外部工具"这个依赖就可以彻底去掉（代价：只能跑 OpenGL RHI 后端）。

用法: python _probe_rendernode.py [expr]
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sympy as sp
from PySide6.QtCore import QPoint, QRectF, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QOpenGLContext
from PySide6.QtOpenGL import (QOpenGLShader, QOpenGLShaderProgram,
                              QOpenGLVertexArrayObject)
from PySide6.QtQml import qmlRegisterType
from PySide6.QtQuick import QQuickItem, QQuickView, QSGRendererInterface, QSGRenderNode

from qmlmathplot.core import ViewRect, widget_shader_sources


class PlotNode(QSGRenderNode):
    """在场景图的 GL 渲染阶段里自己 glDrawArrays（GL_LINE_STRIP）。"""

    def __init__(self) -> None:
        super().__init__()
        self.view: ViewRect | None = None
        self.expr = None
        self.count = 0
        self.size = (0.0, 0.0)
        self.program: QOpenGLShaderProgram | None = None
        self.vao = None
        self.u: dict[str, int] = {}
        self.prepared = False

    # 场景图要知道我们能改哪些状态，好负责恢复
    def changedStates(self):
        flag = QSGRenderNode.StateFlag
        return (flag.ViewportState | flag.RenderTargetState | flag.DepthState
                | flag.ColorState | flag.ScissorState)

    def rect(self) -> QRectF:
        return QRectF(0.0, 0.0, float(self.size[0]), float(self.size[1]))

    def _prepare(self, ctx: QOpenGLContext) -> bool:
        vs, fs = widget_shader_sources(self.expr)
        self.program = QOpenGLShaderProgram()
        v = QOpenGLShader(QOpenGLShader.ShaderTypeBit.Vertex)
        v.compileSourceCode(vs)
        f = QOpenGLShader(QOpenGLShader.ShaderTypeBit.Fragment)
        f.compileSourceCode(fs)
        if not (v.isCompiled() and f.isCompiled()):
            print("着色器编译失败:", v.log(), f.log(), flush=True)
            return False
        self.program.addShader(v)
        self.program.addShader(f)
        if not self.program.link():
            print("链接失败:", self.program.log(), flush=True)
            return False
        # 注意：RHI 的 GL 上下文给的是基类 QOpenGLFunctions（没有 glGenVertexArrays），
        # 所以用 Qt 自己的 VAO 包装类
        self.vao = QOpenGLVertexArrayObject()
        if not self.vao.create():
            print("VAO 创建失败", flush=True)
            return False
        self.vao.bind()
        self.u = {name: self.program.uniformLocation(name) for name in
                  ("u_xMin", "u_xMax", "u_yMin", "u_yMax", "u_count", "u_color")}
        self.prepared = True
        return True

    # 注意：Qt 的 QSGRenderNode 只有 render(state) 这个虚函数（prepare() 不接受上下文），
    # 所以 GL 资源在第一次 render 里建（那时上下文是 current 的）
    def render(self, state) -> None:
        ctx = QOpenGLContext.currentContext()
        if ctx is None or self.view is None:
            return
        if not self.prepared and not self._prepare(ctx):
            return
        funcs = ctx.functions()
        # RenderState 没有 viewportRect（公开 API 里就没有），用 scissorRect 拿像素范围；
        # 拿不到就干脆不设 viewport —— RHI 已经按整个 render target 设好了
        vp = state.scissorRect()
        if vp.width() > 0 and vp.height() > 0:
            funcs.glViewport(int(vp.x()), int(vp.y()), int(vp.width()), int(vp.height()))
        self.program.bind()
        funcs.glUniform1f(self.u["u_xMin"], self.view.xmin)
        funcs.glUniform1f(self.u["u_xMax"], self.view.xmax)
        funcs.glUniform1f(self.u["u_yMin"], self.view.ymin)
        funcs.glUniform1f(self.u["u_yMax"], self.view.ymax)
        funcs.glUniform1i(self.u["u_count"], self.count)
        self.program.setUniformValue(self.u["u_color"], 0.2, 0.8, 1.0)
        funcs.glDrawArrays(0x0003, 0, self.count)   # GL_LINE_STRIP
        self.program.release()


class RawGLPlot(QQuickItem):
    """QML 里的图元：把绘制交给 PlotNode。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.view = ViewRect()
        self.expr = sp.sin(sp.Symbol("x"))
        self.count = 100000

    def updatePaintNode(self, node, _data):
        if node is None:
            node = PlotNode()
        node.view = self.view
        node.expr = self.expr
        node.count = self.count
        node.size = (self.width(), self.height())
        return node


QML = """
import QtQuick
import RawGLProbe 1.0

Item {
    width: 600
    height: 400
    Rectangle { anchors.fill: parent; color: "#14141e" }
    RawGLPlot { anchors.fill: parent }
}
"""


def main() -> int:
    expr_src = sys.argv[1] if len(sys.argv) > 1 else "sin(x)"
    logs: list[str] = []

    def _log(_t, _c, m):
        logs.append(m[:160])
        print("  [qt]", m[:160], flush=True)

    qInstallMessageHandler(_log)

    app = QGuiApplication(sys.argv[:1])
    qmlRegisterType(RawGLPlot, "RawGLProbe", 1, 0, "RawGLPlot")   # 注意：运行时只接受 str
    path = os.path.join(tempfile.gettempdir(), "rawgl_probe.qml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(QML)

    print("[1] 建 QQuickView", flush=True)
    view = QQuickView()
    # 后端由环境变量 QSG_RHI_BACKEND=opengl 决定：QQuickWindow.setGraphicsApi() 在
    # QGuiApplication 之后调用不生效（surface 已按默认后端建好）。也不要自己 setFormat，
    # 那会把深度/模板清掉，GL 后端直接初始化失败。
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(600, 400)
    view.setSource(QUrl.fromLocalFile(path))
    print(f"[2] setSource done, status={view.status()}", flush=True)
    if view.status() != QQuickView.Status.Ready:
        print("QML 错误:", [e.toString() for e in view.errors()])
        return 1

    plot = view.rootObject().findChild(RawGLPlot)
    if plot is None:
        print("没找到 RawGLPlot 图元")
        return 1
    plot.expr = sp.sympify(expr_src)
    plot.view.zoom(0, 0, 0)   # 保持默认视图

    print("[3] show()", flush=True)
    view.show()
    print("[4] shown, pumping", flush=True)
    for _ in range(40):
        app.processEvents()
    # grabWindow() 在"自定义 render node 走 raw GL"时会卡住（实测），所以改用屏幕截图：
    # 窗口可见时抓客户端区域对应的屏幕矩形（逻辑坐标，返回图像按 dpr 放大）
    print("[5] 屏幕截图", flush=True)
    view.setPosition(120, 120)
    for _ in range(10):
        app.processEvents()
    g = view.mapToGlobal(QPoint(0, 0))
    img = app.primaryScreen().grabWindow(0, g.x(), g.y(), view.width(), view.height()).toImage()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_rawgl_probe.png")
    img.save(out)
    bg = (0x14, 0x14, 0x1E)
    lit = sum(1 for y in range(0, img.height(), 4) for x in range(0, img.width(), 4)
              if abs(img.pixelColor(x, y).red() - bg[0])
              + abs(img.pixelColor(x, y).green() - bg[1])
              + abs(img.pixelColor(x, y).blue() - bg[2]) > 20)
    print(f"expr={expr_src} 后端={view.graphicsApi()} 点亮采样点={lit} -> {out}")
    # 数值校验：曲线应穿过 sympy 算出的 y（换算成设备像素）
    v = plot.view
    f = sp.lambdify(sp.Symbol("x"), plot.expr, "math")
    for xw in (0.5, 1.0, 2.0):
        yw = float(f(xw))
        col = int((xw - v.xmin) / (v.xmax - v.xmin) * img.width())
        row = int((v.ymax - yw) / (v.ymax - v.ymin) * img.height())
        hit = [r for r in range(max(0, row - 8), min(img.height(), row + 9))
               if abs(img.pixelColor(col, r).red() - bg[0])
               + abs(img.pixelColor(col, r).green() - bg[1])
               + abs(img.pixelColor(col, r).blue() - bg[2]) > 20]
        got = round(sum(hit) / len(hit), 1) if hit else None
        print(f"  数值校验 x={xw}: 期望行 {row}, 命中 {got}")
    print("qt 消息:", logs[:3] if logs else "无")
    view.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
