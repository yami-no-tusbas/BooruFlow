"""Make the bundled Qt DLL directory explicit before PySide6 is imported."""

from __future__ import annotations

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    qt_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "PySide6"
    if qt_dir.is_dir():
        os.add_dll_directory(str(qt_dir))
        os.environ["PATH"] = str(qt_dir) + os.pathsep + os.environ.get("PATH", "")
