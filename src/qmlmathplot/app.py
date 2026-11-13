"""MVP entry point: wraps the component into a minimal verifiable window.

Reachable as the installed ``qmlmathplot`` console script, as ``python -m qmlmathplot``, and as
``examples/minimal.py``:

    python -m qmlmathplot --backend d3d11 "sin(1/x)"
    uv run qmlmathplot "tan(x)"

The RHI backend comes from the launch argument; without it, Qt's default backend is used. When
specified it must be written to ``QSG_RHI_BACKEND`` *before* ``QGuiApplication`` — Qt reads it
during platform initialization, so calling ``QQuickWindow.setGraphicsApi()`` later has no effect
(the window's surface has already been created for the default backend).
"""

from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from .plot import Plot
from .view import qml_component_path, register_qml_types

# Valid QSG_RHI_BACKEND values (Qt 6); unset = Qt default (d3d11 on Windows)
BACKENDS = ("d3d11", "d3d12", "vulkan", "metal", "opengl", "null")

DEFAULT_EXPRESSION = "sin(x)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qmlmathplot",
        description="Minimal verifiable window for QMLMathPlot (drag to pan, wheel to zoom).",
    )
    parser.add_argument("expression", nargs="?", default=DEFAULT_EXPRESSION,
                        help=f"expression in sympy syntax (default {DEFAULT_EXPRESSION})")
    parser.add_argument("--backend", choices=BACKENDS, default=None,
                        help="RHI backend for Qt Quick; Qt's default when not specified")
    parser.add_argument("--render-mode", choices=("gpu", "cpu"), default="gpu",
                        help="curve renderer: RHI shaders or QPainter paths (default gpu)")
    parser.add_argument("--software", action="store_true",
                        help="use Qt Quick's software scene-graph backend (implies --render-mode cpu)")
    parser.add_argument("--width", type=int, default=900, help="window width (default 900)")
    parser.add_argument("--height", type=int, default=600, help="window height (default 600)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if args.software:
        # Must be set before QGuiApplication (Qt reads the backend during platform init).
        os.environ["QT_QUICK_BACKEND"] = "software"
        args.render_mode = "cpu"
    elif args.backend is not None:
        # An explicitly given backend wins over the environment: the command line overrides all
        os.environ["QSG_RHI_BACKEND"] = args.backend

    app = QGuiApplication(sys.argv[:1])
    register_qml_types()

    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setTitle(f"QMLMathPlot — {args.expression}")
    view.resize(args.width, args.height)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    if view.status() is not QQuickView.Status.Ready:
        for err in view.errors():
            print(err.toString(), file=sys.stderr)
        return 1

    root = view.rootObject()
    if root is None:
        print("failed to create the QML root object", file=sys.stderr)
        return 1

    # MVVM: the app side owns the model and injects it into the component (which also
    # brings its own when nothing is injected)
    try:
        from .calc import SymPyProgramSource
    except ImportError:  # pragma: no cover - the CLI is a developer tool
        print("the command line needs the calculator extra: pip install 'qmlmathplot[calc]'",
              file=sys.stderr)
        return 1
    plot = Plot(program_source=SymPyProgramSource())
    plot.renderMode = args.render_mode
    plot.add_curve(args.expression)
    root.setProperty("plot", plot)

    view.show()
    print(f"RHI backend: {view.graphicsApi()}  (QSG_RHI_BACKEND={os.environ.get('QSG_RHI_BACKEND', 'not set')})",
          flush=True)
    return app.exec()
