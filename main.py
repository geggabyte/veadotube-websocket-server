#!/usr/bin/env python3
"""Entry point for both `python main.py` and the PyInstaller build."""

import multiprocessing
import sys

from veadobridge.app import main

if __name__ == "__main__":
    # Harmless when running from source, required so a frozen build never
    # re-launches its own window if anything spawns a process.
    multiprocessing.freeze_support()
    sys.exit(main())
