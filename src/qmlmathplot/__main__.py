"""``python -m qmlmathplot`` — the same entry point as the installed ``qmlmathplot`` script.

Keeping it here means the command works even when the console script is not on ``PATH`` (a
``pip install --user``, a virtualenv used through ``python -m``, or a checkout run with
``uv run``).
"""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
