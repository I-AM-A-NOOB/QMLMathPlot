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
def _sympy_program_source(monkeypatch):
    """Give ``Plot()``/``Curve()`` a SymPy source in tests.

    The production default is *no* source (the core carries no process and no SymPy policy);
    the suite injects the synchronous SymPy frontend unless a test passes ``program_source``
    explicitly (``None`` included, which is how the missing-source error is tested).
    """
    from qmlmathplot.calc import SymPyProgramSource
    from qmlmathplot.curve import Curve, CurveListModel
    from qmlmathplot.plot import Plot

    source = SymPyProgramSource()
    for cls in (Plot, Curve, CurveListModel):
        original = cls.__init__

        def patched(self, *args, _original=original, **kwargs):
            kwargs.setdefault("program_source", source)
            _original(self, *args, **kwargs)

        monkeypatch.setattr(cls, "__init__", patched)
    yield source


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
