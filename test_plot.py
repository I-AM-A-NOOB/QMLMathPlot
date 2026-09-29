"""QWidget 前端：QOpenGLWidget + 顶点着色器里直接算 y 的折线绘图。

表达式 -> GLSL 与视图数学在 qmlmathplot.core（与 QML 前端共用同一份），
这里只负责"用 QOpenGLWidget 画 GL_LINE_STRIP"。

    uv sync              # 安装依赖和本包（editable）
    python test_plot.py
"""

import sys
from typing import TYPE_CHECKING, Optional

import sympy as sp
from PySide6.QtCore import Property, QObject, QPoint, Qt
from PySide6.QtOpenGL import (
    QOpenGLShader,
    QOpenGLShaderProgram,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication, QMainWindow

from qmlmathplot.core import ViewRect, widget_shader_sources

if TYPE_CHECKING:
    from PySide6.QtOpenGL import QOpenGLFunctions


class SympyGLPlotter(QOpenGLWidget):
    # 类型注解：成员变量
    program: QOpenGLShaderProgram
    vao: int
    u_xMin: int
    u_xMax: int
    u_yMin: int
    u_yMax: int
    u_count: int
    u_color: int

    def __init__(self, expr, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.expr = expr  # SymPy 表达式，例如 sp.sin(x) / x

        # 视图范围（世界坐标）与平移/缩放数学：与 QML 前端共用同一份实现
        self.view = ViewRect()

        # 顶点数量（100k 个点，GPU 并行计算毫无压力）
        self.num_points = 100000

        # 鼠标交互记录
        self.last_mouse = QPoint()

        self.setMinimumSize(800, 600)

    # ---- 视图范围：保留 xMin/xMax/... 的读写接口，内部只有 self.view 一份状态 ----
    def _get_xmin(self) -> float:
        return self.view.xmin

    def _set_xmin(self, value: float) -> None:
        self.view.xmin = value

    def _get_xmax(self) -> float:
        return self.view.xmax

    def _set_xmax(self, value: float) -> None:
        self.view.xmax = value

    def _get_ymin(self) -> float:
        return self.view.ymin

    def _set_ymin(self, value: float) -> None:
        self.view.ymin = value

    def _get_ymax(self) -> float:
        return self.view.ymax

    def _set_ymax(self, value: float) -> None:
        self.view.ymax = value

    xMin = Property(float, _get_xmin, _set_xmin)
    xMax = Property(float, _get_xmax, _set_xmax)
    yMin = Property(float, _get_ymin, _set_ymin)
    yMax = Property(float, _get_ymax, _set_ymax)

    def initializeGL(self):
        """初始化 OpenGL 着色器"""
        # 创建着色器程序
        self.program = QOpenGLShaderProgram()

        # 顶点/片元着色器由 core 生成：顶点着色器用 gl_VertexID 反推 x、算 y，
        # 再映射到 NDC；无穷/非数/超大值直接丢到裁剪空间外断开折线。
        vs_code, fs_code = widget_shader_sources(self.expr)

        vertex_shader = QOpenGLShader(QOpenGLShader.Vertex)
        vertex_shader.compileSourceCode(vs_code)
        if not vertex_shader.isCompiled():
            print("顶点着色器编译失败:", vertex_shader.log())
            return

        fragment_shader = QOpenGLShader(QOpenGLShader.Fragment)
        fragment_shader.compileSourceCode(fs_code)
        if not fragment_shader.isCompiled():
            print("片段着色器编译失败:", fragment_shader.log())
            return

        # 链接程序
        self.program.addShader(vertex_shader)
        self.program.addShader(fragment_shader)
        self.program.link()
        if not self.program.isLinked():
            print("着色器链接失败:", self.program.log())
            return

        # 获取 uniform 句柄（缓存起来，避免每帧查找）
        self.u_xMin = self.program.uniformLocation("u_xMin")
        self.u_xMax = self.program.uniformLocation("u_xMax")
        self.u_yMin = self.program.uniformLocation("u_yMin")
        self.u_yMax = self.program.uniformLocation("u_yMax")
        self.u_count = self.program.uniformLocation("u_count")
        self.u_color = self.program.uniformLocation("u_color")

        # 现代 OpenGL 核心模式需要一个 VAO 才能绘制（哪怕它是空的）
        self.vao = self.context().functions().glGenVertexArrays()
        self.context().functions().glBindVertexArray(self.vao)

    def paintGL(self):
        """每帧刷新（拖动/缩放时高频触发）"""
        if not self.program or not self.program.isLinked():
            return

        funcs: QOpenGLFunctions = self.context().functions()
        funcs.glClearColor(0.08, 0.08, 0.12, 1.0)  # 深色科技风背景
        funcs.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT

        self.program.bind()

        # 上传 4 个边界和点数到显存（仅 5 个 float/int，CPU 几乎零成本）
        view = self.view
        funcs.glUniform1f(self.u_xMin, view.xmin)
        funcs.glUniform1f(self.u_xMax, view.xmax)
        funcs.glUniform1f(self.u_yMin, view.ymin)
        funcs.glUniform1f(self.u_yMax, view.ymax)
        funcs.glUniform1i(self.u_count, self.num_points)
        self.program.setUniformValue(self.u_color, 0.2, 0.8, 1.0)  # 亮蓝色

        # 绘制线带 (GL_LINE_STRIP = 0x0003)
        funcs.glDrawArrays(0x0003, 0, self.num_points)

        self.program.release()

    def resizeGL(self, w, h):
        """窗口大小变化时修正视口"""
        self.context().functions().glViewport(0, 0, w, h)

    # ------------------- 鼠标交互（丝滑拖动的关键） -------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.last_mouse = event.pos()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            dx = event.pos().x() - self.last_mouse.x()
            dy = event.pos().y() - self.last_mouse.y()

            self.view.pan_pixels(dx, dy, self.width(), self.height())
            self.last_mouse = event.pos()
            self.update()  # 只上传 uniform，GPU 重算所有顶点

    def wheelEvent(self, event):
        """滚轮缩放：光标下的世界点钉住不动，同一个位置上下滚严格可逆"""
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()  # 高精度滚轮/触摸板可能只给 pixelDelta
        if delta == 0:
            return

        u = event.position().x() / self.width()
        v = event.position().y() / self.height()
        self.view.zoom(delta, u, v)
        self.update()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SymPy -> GLSL GPU 实时渲染")

        # ========== 在这里修改你的函数 ==========
        x = sp.Symbol("x")
        # 试试这些：
        expr = sp.sin(1 / x)  # 经典 sinc
        # expr = sp.exp(-x**2) * sp.sin(10*x)  # 衰减震荡
        # expr = sp.Piecewise((x, x < 0), (x**2, True))  # 分段函数测试
        # expr = sp.gamma(x)  # 伽马函数（如果 GLSL 不支持，会报错，慎用）
        # =====================================

        self.plotter = SympyGLPlotter(expr)
        self.setCentralWidget(self.plotter)
        self.resize(900, 600)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
