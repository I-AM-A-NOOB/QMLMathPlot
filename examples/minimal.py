"""Minimal example: open a standalone window.

    python examples/minimal.py --backend d3d11 "sin(1/x)"
    uv run qmlmathplot "tan(x)"          # equivalent (command-line entry point)

Left-drag to pan; the wheel zooms anchored at the cursor. See ``qmlmathplot.app``
for the implementation.
"""

import sys

from qmlmathplot.app import main

if __name__ == "__main__":
    sys.exit(main())
