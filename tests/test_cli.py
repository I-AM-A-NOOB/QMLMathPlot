"""The package must be runnable as a module, not only through the console script.

A subprocess, because the test session owns a ``QApplication`` (a second one in the same process
is fatal) — the same reason ``test_export.py`` uses one.
"""

from __future__ import annotations

import subprocess
import sys


def test_python_dash_m_runs_the_entry_point() -> None:
    """``python -m qmlmathplot`` is the documented way in (README's entry table): if
    ``__main__.py`` is missing it fails with "No module named qmlmathplot.__main__", which is
    exactly what happened before it was added."""
    done = subprocess.run([sys.executable, "-m", "qmlmathplot", "--help"],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert "usage" in done.stdout.lower(), done.stdout
    assert "--backend" in done.stdout
