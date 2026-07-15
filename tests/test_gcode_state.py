"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

from fakes import FakeGcode

from eddy_seek.movement.gcode_state import (
    GCodeState,
    restore_gcode_state,
    save_gcode_state,
)


def test_restore_gcode_state_move_inserts_dwell():
    gcode = FakeGcode()
    restore_gcode_state(gcode, "test_state", move=True)
    assert gcode.scripts == [
        "G4 P1",
        "RESTORE_GCODE_STATE NAME=test_state MOVE=1",
    ]


def test_restore_gcode_state_without_move_skips_dwell():
    gcode = FakeGcode()
    restore_gcode_state(gcode, "test_state")
    assert gcode.scripts == ["RESTORE_GCODE_STATE NAME=test_state"]


def test_saved_gcode_state_context_manager():
    gcode = FakeGcode()
    with GCodeState(gcode, "ctx_state", move_on_restore=True):
        save_gcode_state(gcode, "ignored")
    assert gcode.scripts[0] == "SAVE_GCODE_STATE NAME=ctx_state"
    assert gcode.scripts[-2:] == [
        "G4 P1",
        "RESTORE_GCODE_STATE NAME=ctx_state MOVE=1",
    ]
