"""Compile a :class:`~qmlmathplot.protocol.Program` source into a callable.

Programs travel as text: ``math`` sources for scalar sampling, ``numpy`` sources for
vectorised work. Both are compiled against a namespace that holds only the module and
``abs`` — no builtins, so a program can reach nothing the calculator did not emit.
Compilation is ~13 µs; a compiled call costs the same as a ``lambdify`` function.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

__all__ = ["compile_function"]


def compile_function(
    source: str, name: str = "f", *, numpy: bool = False
) -> Callable[..., Any]:
    """Return the ``name`` function defined by ``source``."""
    # Explicit empty builtins: the calculator is the trust boundary, and a compiled program
    # must not reach ``open``/``__import__`` even if it somehow contained them.
    if numpy:
        import numpy as np

        namespace: dict[str, Any] = {"numpy": np, "abs": abs, "__builtins__": {}}
    else:
        namespace = {"math": math, "abs": abs, "__builtins__": {}}
    exec(compile(source, "<program>", "exec"), namespace)  # noqa: S102 - our source
    function = namespace.get(name)
    if not callable(function):
        raise ValueError(f"the program source does not define {name}()")
    return function
