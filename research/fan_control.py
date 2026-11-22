"""Compatibility shim for manually reviewed Gen 8 research scripts only.

Importing this module does not open the driver. Executing any research script
that instantiates and opens this class performs real hardware access.
"""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from ec.winring0_backend import WinRing0Backend as TransientWinRing0

__all__ = ["TransientWinRing0"]
