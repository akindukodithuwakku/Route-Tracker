"""
Path resolution that works both when running from source (`py agent_main.py`)
and when frozen into an exe by PyInstaller. Writable state (config, local
queue db, logs) always lives next to the actual exe/script.
"""

import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent
