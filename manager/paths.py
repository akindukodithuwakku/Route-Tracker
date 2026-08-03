"""
Path resolution that works both when running from source (`py app.py`) and
when frozen into an exe by PyInstaller. Writable state (config, database,
certs, logs) always lives next to the actual exe/script -- never inside
PyInstaller's temp extraction dir -- so it survives restarts and upgrades.
"""

import sys
from pathlib import Path


def base_dir() -> Path:
    """Where writable state lives: next to the exe when frozen, next to
    this source file otherwise."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def bundle_dir() -> Path:
    """Where read-only bundled assets (static/templates) live: PyInstaller's
    extraction dir when frozen, next to this source file otherwise."""
    return Path(getattr(sys, "_MEIPASS", str(Path(__file__).parent)))
