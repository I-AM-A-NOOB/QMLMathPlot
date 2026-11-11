"""With SymPy unavailable, the core still imports and ``add_program`` still works.

Run in subprocesses so an environment where SymPy is installed cannot mask the result: a
meta-path blocker makes ``import sympy`` fail, then the core and the matplotlib hand-off are
exercised.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

BLOCKER = '''
import sys


class _BlockSymPy:
    def find_spec(self, name, path=None, target=None):
        if name == "sympy" or name.startswith("sympy."):
            raise ModuleNotFoundError("sympy is blocked for this test")
        return None

sys.meta_path.insert(0, _BlockSymPy())
'''

CORE_SCRIPT = BLOCKER + textwrap.dedent(
    """
    import qmlmathplot
    from qmlmathplot import Plot, Program, ProgramSourceMissingError
    from qmlmathplot.view import qml_component_path     # the PlotView module

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

    print("qml", bool(qml_component_path()))
    print("sympy", "sympy" in sys.modules)
    print("calc", end=" ")
    try:
        import qmlmathplot.calc  # noqa: F401
    except ImportError:
        print("blocked")
    """
)

MATPLOTLIB_SCRIPT = BLOCKER + textwrap.dedent(
    """
    from qmlmathplot import Plot, Program

    program = Program(
        text="sin(x)", kind="adaptive",
        numpy="def f(x):\\n    return numpy.sin(x)\\n",
    )
    plot = Plot(program_source=None)
    plot.add_program(program)
    figure = plot.to_matplotlib(samples=20)
    line = figure.axes[0].lines[0]
    print("points", len(line.get_xdata()))
    print("sympy", "sympy" in sys.modules)
    """
)


def _run(script: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=180
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    return output


def test_import_and_add_program_survive_without_sympy() -> None:
    output = _run(CORE_SCRIPT)
    assert "program True" in output, output
    assert "missing-source ok" in output, output
    assert "sympy False" in output, output
    assert "calc blocked" in output, output


def test_the_matplotlib_handoff_works_without_sympy() -> None:
    output = _run(MATPLOTLIB_SCRIPT)
    assert "points 20" in output, output
    assert "sympy False" in output, output
