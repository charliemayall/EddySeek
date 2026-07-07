"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

SAVE_GCODE_STATE / RESTORE_GCODE_STATE helpers.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from klippy.gcode import GCodeDispatch

# 1 ms dwell before MOVE restore; enough to clear Klipper's move timer
_MOVE_RESTORE_DWELL_MS = 1


def save_gcode_state(gcode: GCodeDispatch, name: str) -> None:
    gcode.run_script_from_command(f"SAVE_GCODE_STATE NAME={name}")


def restore_gcode_state(gcode: GCodeDispatch, name: str, *, move: bool = False) -> None:
    if move:
        gcode.run_script_from_command(f"G4 P{_MOVE_RESTORE_DWELL_MS}")
        gcode.run_script_from_command(f"RESTORE_GCODE_STATE NAME={name} MOVE=1")
    else:
        gcode.run_script_from_command(f"RESTORE_GCODE_STATE NAME={name}")


@contextmanager
def GCodeState(
    gcode: GCodeDispatch, name: str, *, move_on_restore: bool = False
) -> Iterator[None]:
    save_gcode_state(gcode, name)
    try:
        yield
    finally:
        restore_gcode_state(gcode, name, move=move_on_restore)
