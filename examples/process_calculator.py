"""Qt Quick with the calculator in its own process: the isolation example.

    python examples/process_calculator.py                  # show the window
    python examples/process_calculator.py --check-isolation  # assert no sympy in this process

The library never starts a process: this launcher asks the worker for a ``Program``, then
hands it to the model with ``Plot.add_program``. The template for a real host (Symplify) is
the same: its worker produces programs, the UI only consumes data.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from qmlmathplot import Plot, Program, qml_component_path, register_qml_types
from qmlmathplot.protocol import (
    decode_frames,
    encode_frame,
    parse_response,
    request_payload,
)

__all__ = ["request_program"]


def request_program(text: str, *, timeout: float = 30.0) -> Program:
    """One synchronous round trip with ``python -m qmlmathplot.calc`` (host-side helper)."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [sys.executable, "-m", "qmlmathplot.calc"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(encode_frame(request_payload(1, text)))
        process.stdin.flush()
        buffer = b""
        while True:
            chunk = process.stdout.read1(65536) if hasattr(process.stdout, "read1") \
                else process.stdout.read(65536)
            if not chunk:
                raise RuntimeError("the calculator process exited without an answer")
            buffer += chunk
            frames, buffer = decode_frames(buffer)
            if frames:
                _request_id, program = parse_response(frames[0])
                return program
    finally:
        process.terminate()
        process.wait(timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expression", nargs="?", default="sin(1/x)")
    parser.add_argument(
        "--check-isolation",
        action="store_true",
        help="build the program in a child process, then assert sympy was never imported here",
    )
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=600)
    arguments = parser.parse_args()

    app = QGuiApplication(sys.argv[:1])
    register_qml_types()

    program = request_program(arguments.expression)

    plot = Plot(program_source=None)
    curve = plot.add_program(program)

    if arguments.check_isolation:
        deadline = time.monotonic() + 30.0
        while not (curve.computeShader.toString() or curve.fragmentShader.toString()):
            app.processEvents()
            if curve.error or time.monotonic() > deadline:
                break
            time.sleep(0.005)
        leaked = "sympy" in sys.modules
        print(f"program: {program.kind}; error: {curve.error!r}; "
              f"sympy in UI process: {leaked}")
        return 0 if not leaked and not curve.error else 1

    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(arguments.width, arguments.height)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    if view.status() is not QQuickView.Status.Ready:
        print("the QML component failed to load", file=sys.stderr)
        return 1
    view.rootObject().setProperty("plot", plot)
    view.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
