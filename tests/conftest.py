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
from qmlmathplot.curve import Curve


@pytest.fixture(autouse=True)
def _synchronous_bakes(monkeypatch):
    """Deterministic tests: qsb runs inline. The async path has its own test."""
    monkeypatch.setattr(Curve, "bake_async", False)


@pytest.fixture(scope="session")
def app() -> QApplication:
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = QApplication(sys.argv[:1])
    register_qml_types()
    return application
