"""The UI process stays sympy-free: the core never imports the calculator.

Run in a subprocess so the test session's own imports cannot mask the result.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    """
    import sys

    from qmlmathplot import Plot, Program, ProgramSourceMissingError

    print("imported", "sympy" in sys.modules)

    plot = Plot(program_source=None)
    program = Program(
        text="sin(x)", kind="adaptive",
        math="def f(x):\\n    return math.sin(x)\\n",
    )
    curve = plot.add_program(program)
    print("program", curve.program is program)

    try:
        plot.add_curve("sin(x)")
    except ProgramSourceMissingError:
        print("missing-source ok")

    print("after", "sympy" in sys.modules)
    """
)


def test_the_ui_process_never_imports_sympy() -> None:
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=120
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "imported False" in output, output
    assert "program True" in output, output
    assert "missing-source ok" in output, output
    assert "after False" in output, output
