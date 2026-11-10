"""Minimal QtWidgets host: the library's ``MathPlotWidget`` in an ordinary layout.

    python examples/widget_minimal.py "tan(x)"
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from qmlmathplot import MathPlotWidget


def main() -> int:
    app = QApplication(sys.argv[:1])
    window = QWidget()
    layout = QVBoxLayout(window)
    layout.addWidget(MathPlotWidget(sys.argv[1] if len(sys.argv) > 1 else "sin(x)"))
    window.resize(900, 600)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
