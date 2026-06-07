"""
Entry point for the ``kb`` command installed via ``pip install -e .``

This shim exists because the CLI lives in builder/src/cli.py (a name too
generic for a direct setuptools entry point). It simply imports and calls
the real main() from there.
"""

import os
import sys

# Ensure builder/src/ is on the path so ``from core.xxx import ...``
# works whether kb is invoked as a script or via the installed entry point.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from cli import main  # noqa: E402


def main():  # re-export for setuptools entry_points
    from cli import main as _main
    _main()
