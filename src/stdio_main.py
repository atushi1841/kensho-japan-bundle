"""Unified entry point for the Kensho Japan Data Bundle."""
from __future__ import annotations

import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.server import server


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    server.run()


if __name__ == "__main__":
    main()
