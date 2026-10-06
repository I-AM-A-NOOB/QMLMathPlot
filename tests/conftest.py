"""Shared fixtures: a single ``QApplication`` for the whole test session.

Using ``QApplication`` instead of ``QGuiApplication``: the former is a subclass of
the latter, so the QML-side tests work as usual, while the QtWidgets side
(``MathPlotWidget``) needs it. A process may only hold one ``QCoreApplication``
instance, so it must be shared at session scope.
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
