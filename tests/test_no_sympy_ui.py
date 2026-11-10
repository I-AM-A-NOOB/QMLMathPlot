"""The UI process stays sympy-free when the calculator is a separate process.

Run in a subprocess so the test session's own imports cannot mask the result: the point is
that ``import qmlmathplot`` + ``Plot`` + ``add_curve`` with a ``ProcessCalculator`` never
loads sympy in the UI process.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    """
    import sys
    import time

    from PySide6.QtCore import QCoreApplication

    from qmlmathplot import Plot
    from qmlmathplot.client import ProcessCalculator

    print("imported", "sympy" in sys.modules)

    app = QCoreApplication([])
    plot = Plot()
    calculator = ProcessCalculator()
    plot.calculator = calculator
    try:
        curve = plot.add_curve("sin(x)")
        deadline = time.time() + 30.0
        while not curve.computeShader.toString() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.005)
        print("compute", bool(curve.computeShader.toString()))
        print("error", repr(curve.error))
        print("after", "sympy" in sys.modules)
    finally:
        calculator.close()
    """
)


def test_the_ui_process_never_imports_sympy() -> None:
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "imported False" in output, output
    assert "compute True" in output, output
    assert "error ''" in output, output
    assert "after False" in output, output
