"""共享 fixture：整个测试会话只建一个 ``QApplication``。

用 ``QApplication`` 而不是 ``QGuiApplication``：前者是后者的子类，QML 侧测试照常
工作，而 QtWidgets 侧（``MathPlotWidget``）需要它。一个进程只能有一个
``QCoreApplication`` 实例，所以必须在会话级共享。
"""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication

from qmlmathplot import register_qml_types


@pytest.fixture(scope="session")
def app() -> QApplication:
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = QApplication(sys.argv[:1])
    register_qml_types()
    return application
