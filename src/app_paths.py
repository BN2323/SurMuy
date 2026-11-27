"""Locate shared data from source or a versioned Windows build."""

from pathlib import Path
import sys


if getattr(sys, "frozen", False):
    executable_dir = Path(sys.executable).resolve().parent
    APP_DIR = executable_dir.parent if executable_dir.name == "builds" else executable_dir
else:
    APP_DIR = Path(__file__).resolve().parents[1]
