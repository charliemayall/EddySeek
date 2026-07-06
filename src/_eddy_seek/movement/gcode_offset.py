"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Gcode XY offset helpers for seek sessions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..common import Offset

if TYPE_CHECKING:
    from klippy.klippy import Printer


def clear_gcode_offset_xy(printer: Printer) -> None:
    """Zero XY gcode offset so alignment moves use machine coordinates."""
    printer.lookup_object("gcode").run_script_from_command(
        f"SET_GCODE_OFFSET {Offset.zero().to_gcode()}"
    )
