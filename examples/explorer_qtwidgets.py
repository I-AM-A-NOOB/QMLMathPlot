"""A slightly more detailed example: a function input box plus a ring of event-grabbing
neighbours, to verify that the plot widget does not fight with them.

    python examples/explorer_qtwidgets.py

Deliberately puts the plot widget in a conflict-prone environment, verified both by eye
and by the log:

* **Input box QLineEdit**: Enter or the "Draw" button switches the expression. The plot
  area is ``ClickFocus`` (not in the Tab focus chain), so focus is not stolen while
  typing; on a bad expression the last working plot is kept and the sympy error appears
  in red.
* **Splitter QSplitter + text box**: dragging the splitter resizes the plot area — the plot
  fills its pane at any size — to see whether repaint and stacking behave. (The "does the
  plot steal the wheel/drag from a scrollable parent?" case needs an oversized plot, so it
  lives in ``tests/test_widget.py`` and ``tests/test_qtquick_coexistence.py`` instead of in
  this demo.)
* **Overlay**: a translucent QLabel over the plot area's upper-left corner (with
  ``WA_TransparentForMouseEvents``), verifying QQuickWidget stacking with sibling widgets
  and mouse pass-through.
* **Event log** (right): which widget each wheel/focus event lands on, one line at a
  time, so whoever grabbed it is obvious.
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
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from qmlmathplot import MathPlotWidget

#: Aspect choices: label -> the value handed to ``MathPlotWidget.aspect``.
ASPECTS = (
    ("Follow view", "view"),
    ("1:1 (square)", 1.0),
    ("2:1", 2.0),
    ("1:2", 0.5),
)

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
    """An input box + scroll area + splitter + overlay, ringing the plot widget."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("QMLMathPlot — widget coexistence check")
        self.resize(1200, 760)

        # ---------------------------------------------------------- top-bar widgets
        self.input = QLineEdit("sin(1/x)")
        self.input.setPlaceholderText("sympy syntax: sin(1/x) / tan(x) / log(x) …")
        self.input.setMinimumWidth(260)
        self.input.returnPressed.connect(self.draw)

        self.samples = QComboBox()
        self.samples.addItems(SAMPLES)
        self.samples.activated.connect(self.pick_sample)

        self.aspects = QComboBox()
        for label, value in ASPECTS:
            self.aspects.addItem(label, value)
        self.aspects.activated.connect(self.pick_aspect)

        draw = QPushButton("Draw")
        draw.clicked.connect(self.draw)
        reset = QPushButton("Reset view")
        reset.clicked.connect(lambda: self.plot.reset_view())

        self.error = QLabel("")
        self.error.setStyleSheet("color: #ff8080")

        top = QHBoxLayout()
        top.addWidget(QLabel("f(x) ="))
        top.addWidget(self.input, 1)
        top.addWidget(draw)
        top.addWidget(self.samples)
        top.addWidget(QLabel("aspect:"))
        top.addWidget(self.aspects)
        top.addWidget(reset)
        top.addWidget(self.error, 1)

        # ------------------------------------------------------ the plot widget itself
        self.plot = MathPlotWidget(self.input.text())
        self.plot.expressionChanged.connect(self.sync_status)
        self.plot.errorChanged.connect(self.sync_status)
        self.plot.controller.viewChanged.connect(self.sync_status)

        # overlay: stacked over the plot area, but does not consume mouse events
        self.overlay = QLabel("Overlay QLabel (translucent, click-through)", self.plot)
        self.overlay.setStyleSheet(
            "background: rgba(255,255,255,28); color: #dddddd; padding: 4px 8px;"
            "border: 1px solid rgba(255,255,255,60); border-radius: 4px;"
        )
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.overlay.move(12, 12)
        self.overlay.raise_()

        # ------------------------------------------------------------ right-hand log
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setPlainText(
            "Event log (which widget each wheel / focus event lands on):\n"
            "· wheel over the plot area = zoom, drag over it = pan\n"
            "· focus moves only between the input box / text box; the plot area is not in the Tab chain\n"
        )

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.plot)
        split.addWidget(self.log)
        split.setSizes([880, 320])

        central = QWidget()
        box = QVBoxLayout(central)
        box.addLayout(top)
        box.addWidget(split, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.aspects.setCurrentIndex(1)          # start at 1:1: no distortion on resize
        self.pick_aspect()

        # global event filter: log who received the wheel/focus (pure observation, no behaviour change)
        app = QApplication.instance()
        assert app is not None
        app.installEventFilter(self)

    # ------------------------------------------------------------------ slots
    def pick_aspect(self) -> None:
        value = self.aspects.currentData()
        self.plot.aspect = value
        self.log_line(f"aspect -> {self.aspects.currentText()}")
        self.sync_status()

    def pick_sample(self) -> None:
        self.input.setText(self.samples.currentText())
        self.draw()

    def draw(self) -> None:
        self.plot.expression = self.input.text()
        self.log_line(f"draw {self.input.text()!r}")
        self.sync_status()

    def sync_status(self) -> None:
        xmin, xmax, ymin, ymax = self.plot.view_bounds()
        self.error.setText(self.plot.error)
        self.statusBar().showMessage(
            f"x ∈ [{xmin:.6g}, {xmax:.6g}]   y ∈ [{ymin:.6g}, {ymax:.6g}]   "
            f"(drag to pan / wheel zooms anchored at the cursor)"
        )

    def log_line(self, text: str) -> None:
        self.log.appendPlainText(text)

    # ------------------------------------------------------- event observation
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            self.log_line(f"wheel -> {type(watched).__name__}")
        elif event.type() == QEvent.Type.FocusIn and isinstance(watched, QWidget):
            self.log_line(f"focus -> {type(watched).__name__}")
        return super().eventFilter(watched, event)


def main() -> int:
    app = QApplication(sys.argv[:1])
    window = Explorer()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
