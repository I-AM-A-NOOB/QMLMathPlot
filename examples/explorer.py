"""稍详细的示例：函数输入框 + 一圈"会抢事件"的邻居，用来验证绘图控件会不会打架。

    python examples/explorer.py

刻意把绘图控件放进容易冲突的环境，肉眼 + 日志双重验证：

* **输入框 QLineEdit**：回车或点"绘制"换表达式。绘图区是 ``ClickFocus``（不进 Tab
  焦点链），所以连续输入时焦点不会被抢；输错时保留上一份可用图形，红字给出 sympy 报错。
* **滚动区 QScrollArea**：绘图区 1200×800 比视口大。滚轮落在**绘图区**上是缩放
  （QML 侧已 ``accepted``，事件不会漏给滚动区）；落在**滚动条**上才是滚动。
  这是 widgets 世界里最容易打架的一处。
* **分割条 QSplitter + 文本框**：拖动分割条改变绘图区大小，看重绘与叠放是否正常。
* **覆盖层**：半透明 QLabel 叠在绘图区左上角（且 ``WA_TransparentForMouseEvents``），
  验证 QQuickWidget 与兄弟控件的叠放、以及鼠标穿透。
* **事件日志**（右侧）：滚轮/焦点事件落在哪个控件上，一行一行写出来，谁抢到一目了然。
"""

from __future__ import annotations

import sys
from typing import final

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from qmlmathplot import MathPlotWidget

SAMPLES = (
    "sin(x)",
    "sin(1/x)",
    "tan(x)",
    "log(x)",
    "1/x",
    "x^2",
    "exp(-x*x)*sin(10*x)",
    "asin(x)",
    "sqrt(x)",
)


@final
class Explorer(QMainWindow):
    """输入框 + 滚动区 + 分割条 + 覆盖层，围着绘图控件一圈。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("QMLMathPlot — 控件共存验证")
        self.resize(1200, 760)

        # ---------------------------------------------------------- 顶栏控件
        self.input = QLineEdit("sin(1/x)")
        self.input.setPlaceholderText("sympy 语法：sin(1/x) / tan(x) / log(x) …")
        self.input.setMinimumWidth(260)
        self.input.returnPressed.connect(self.draw)

        self.samples = QComboBox()
        self.samples.addItems(SAMPLES)
        self.samples.activated.connect(self.pick_sample)

        draw = QPushButton("绘制")
        draw.clicked.connect(self.draw)
        reset = QPushButton("重置视图")
        reset.clicked.connect(lambda: self.plot.reset_view())

        self.error = QLabel("")
        self.error.setStyleSheet("color: #ff8080")

        top = QHBoxLayout()
        top.addWidget(QLabel("f(x) ="))
        top.addWidget(self.input, 1)
        top.addWidget(draw)
        top.addWidget(self.samples)
        top.addWidget(reset)
        top.addWidget(self.error, 1)

        # ------------------------------------------------------ 绘图控件本体
        self.plot = MathPlotWidget(self.input.text())
        self.plot.setMinimumSize(1200, 800)          # 比视口大 -> 滚动区真能滚
        self.plot.expressionChanged.connect(self.sync_status)
        self.plot.errorChanged.connect(self.sync_status)
        self.plot.controller.viewChanged.connect(self.sync_status)

        # 覆盖层：叠在绘图区上，但不吃鼠标事件
        self.overlay = QLabel("覆盖层 QLabel（半透明、鼠标穿透）", self.plot)
        self.overlay.setStyleSheet(
            "background: rgba(255,255,255,28); color: #dddddd; padding: 4px 8px;"
            "border: 1px solid rgba(255,255,255,60); border-radius: 4px;"
        )
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.overlay.move(12, 12)
        self.overlay.raise_()

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.plot)
        self.scroll_area.setWidgetResizable(False)        # 保持绘图区 1200×800
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ------------------------------------------------------------ 右侧日志
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setPlainText(
            "事件日志（滚轮 / 焦点落在哪个控件上）：\n"
            "· 滚轮在绘图区 = 缩放；滚轮在滚动条 = 滚动\n"
            "· 焦点只在输入框/文本框之间转移，绘图区不进 Tab 链\n"
        )

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.scroll_area)
        split.addWidget(self.log)
        split.setSizes([880, 320])

        central = QWidget()
        box = QVBoxLayout(central)
        box.addLayout(top)
        box.addWidget(split, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.sync_status()

        # 全局事件过滤器：记录谁收到了滚轮/焦点（纯观察，不改行为）
        app = QApplication.instance()
        assert app is not None
        app.installEventFilter(self)

    # ------------------------------------------------------------------ 槽
    def pick_sample(self) -> None:
        self.input.setText(self.samples.currentText())
        self.draw()

    def draw(self) -> None:
        self.plot.expression = self.input.text()
        self.log_line(f"绘制 {self.input.text()!r}")
        self.sync_status()

    def sync_status(self) -> None:
        xmin, xmax, ymin, ymax = self.plot.view_bounds()
        self.error.setText(self.plot.error)
        self.statusBar().showMessage(
            f"x ∈ [{xmin:.6g}, {xmax:.6g}]   y ∈ [{ymin:.6g}, {ymax:.6g}]   "
            f"（拖拽平移 / 滚轮以光标为锚点缩放）"
        )

    def log_line(self, text: str) -> None:
        self.log.appendPlainText(text)

    # ------------------------------------------------------- 事件观察
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            self.log_line(f"滚轮 -> {type(watched).__name__}")
        elif event.type() == QEvent.Type.FocusIn and isinstance(watched, QWidget):
            self.log_line(f"焦点 -> {type(watched).__name__}")
        return super().eventFilter(watched, event)


def main() -> int:
    app = QApplication(sys.argv[:1])
    window = Explorer()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
