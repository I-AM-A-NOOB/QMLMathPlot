"""The calculator side: sympy lives here, not in the model.

``build_program`` is Qt-free, so it runs both in-process and inside the worker launched by
``python -m qmlmathplot.calc``.
"""

from __future__ import annotations

from .build import build_program, python_sources

__all__ = ["build_program", "python_sources"]
