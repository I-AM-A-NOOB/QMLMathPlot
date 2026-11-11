"""The calculator side: sympy lives here, not in the model.

``build_program`` is Qt-free, so it runs both in-process and inside the worker launched by
``python -m qmlmathplot.calc``.
"""

from __future__ import annotations

from .build import build_program, python_sources

__all__ = ["SymPyProgramSource", "build_program", "python_sources"]


class SymPyProgramSource:
    """The synchronous SymPy frontend: ``text`` -> :class:`~qmlmathplot.protocol.Program`.

    Pass one explicitly to a plot (``Plot(program_source=SymPyProgramSource())``); the core
    never creates one, so a machine without sympy gets a clear error from ``add_curve``
    instead of an implicit import.
    """

    def program(self, text: str, *, implicit: bool | None = None):
        return build_program(text, implicit=implicit)
