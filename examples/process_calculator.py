"""Qt Quick with the calculator in its own process: the isolation example.

    python examples/process_calculator.py                 # show the window
    python examples/process_calculator.py --check-isolation  # assert no sympy in this process

The library never starts a calculator process itself. This launcher owns it:
``ProcessCalculator`` spawns ``python -m qmlmathplot.calc``, the model consumes the programs
it returns, and ``--check-isolation`` proves that the UI process never imports sympy.
"""

from __future__ import annotations

import argparse
import sys
import time

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from qmlmathplot import Plot, qml_component_path, register_qml_types
from qmlmathplot.client import ProcessCalculator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expression", nargs="?", default="sin(1/x)")
    parser.add_argument(
        "--check-isolation",
        action="store_true",
        help="wait for the first program, then assert sympy was never imported here",
    )
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=600)
    arguments = parser.parse_args()

    app = QGuiApplication(sys.argv[:1])
    register_qml_types()

    plot = Plot()
    calculator = ProcessCalculator(parent=plot)
    plot.calculator = calculator
    curve = plot.add_curve(arguments.expression)

    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(arguments.width, arguments.height)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    if view.status() is not QQuickView.Status.Ready:
        print("the QML component failed to load", file=sys.stderr)
        return 1
    view.rootObject().setProperty("plot", plot)
    view.show()

    def wait_for_program(timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if curve.computeShader.toString() or curve.fragmentShader.toString():
                return True
            if curve.error:
                return False
            time.sleep(0.005)
        return False

    if arguments.check_isolation:
        ready = wait_for_program()
        leaked = "sympy" in sys.modules
        print(f"program ready: {ready}; error: {curve.error!r}; sympy in UI process: {leaked}")
        calculator.close()
        return 0 if ready and not leaked else 1

    try:
        return app.exec()
    finally:
        calculator.close()


if __name__ == "__main__":
    sys.exit(main())
