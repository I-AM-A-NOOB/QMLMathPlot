"""Qt Quick explorer demo: a function input field plus neighbours that compete for events.

    python examples/explorer_qtquick.py

The UI lives in ``examples/explorer_qtquick.qml`` (Qt Quick Controls). This launcher only
registers the QML types and loads that file — the pure Qt Quick path an app takes, with no
QtWidgets involved. Set ``QSG_RHI_BACKEND`` (e.g. ``opengl``) before running to pick the
RHI backend; see ``qmlmathplot.app`` for the rationale.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from qmlmathplot import register_qml_types


def main() -> int:
    app = QGuiApplication(sys.argv[:1])
    register_qml_types()

    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_suffix(".qml"))))
    if not engine.rootObjects():
        print("failed to load explorer_qtquick.qml", file=sys.stderr)
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
